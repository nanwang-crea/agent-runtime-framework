from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent_runtime_framework.assistant.conversation import stream_conversation_reply
from agent_runtime_framework.agents.codex.models import CodexAction, CodexActionResult
from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.run_context import update_loaded_instructions
from agent_runtime_framework.resources import ResourceRef, describe_resource_semantics
from agent_runtime_framework.tools import ToolCall, execute_tool_call

if TYPE_CHECKING:
    from agent_runtime_framework.assistant.session import AssistantSession


def execute_action(loop: Any, action: CodexAction, session: "AssistantSession") -> CodexActionResult:
    executor = loop.context.services.get("action_executor")
    if callable(executor):
        result = executor(action, session, loop.context)
        return normalize_result(result)
    if action.kind == "locate_target":
        return execute_locate_target_action(loop, action)
    if action.kind in {"call_tool", "apply_patch", "create_path", "edit_text", "move_path", "delete_path"}:
        return execute_tool_action(loop, action)
    if action.kind == "run_verification":
        return execute_verification_action(loop, action)
    if action.kind == "respond":
        if bool(action.metadata.get("direct_output")):
            return CodexActionResult(status="completed", final_output=action.instruction)
        diagnostics: dict[str, str | None] = {"source": "fallback", "reason": "unknown"}
        final_output = "".join(stream_conversation_reply(action.instruction, loop.context, session, diagnostics=diagnostics))
        return CodexActionResult(status="completed", final_output=final_output, metadata={"conversation": diagnostics})
    return CodexActionResult(status="failed", final_output=f"unsupported action kind: {action.kind}")


def execute_locate_target_action(loop: Any, action: CodexAction) -> CodexActionResult:
    target_hint = str(action.metadata.get("target_hint") or "").strip()
    root = Path(loop.context.application_context.config.get("default_directory") or "").expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    resolved = root
    if target_hint:
        candidate = Path(target_hint).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=False)
        if not resolved.exists():
            repository = loop.context.application_context.resource_repository
            matches = repository.find_by_name(ResourceRef.for_path(root), target_hint)
            if matches:
                resolved = Path(matches[0].location).expanduser().resolve()
    if not (resolved == root or root in resolved.parents):
        raise ValueError(f"path is outside allowed roots: {resolved}")
    label = str(resolved.relative_to(root)) if resolved != root else root.name
    summary = f"Located target: {label}"
    update_loaded_instructions(loop.context, str(resolved))
    semantics = describe_resource_semantics(ResourceRef.for_path(resolved), loop.context.application_context.resource_repository)
    return CodexActionResult(
        status="completed",
        final_output=summary,
        metadata={
            "tool_output": {
                "path": str(resolved),
                "resolved_path": str(resolved),
                "summary": summary,
                "text": summary,
                "is_directory": resolved.is_dir(),
                "resource_kind": semantics.resource_kind,
                "is_container": semantics.is_container,
                "allowed_actions": list(semantics.allowed_actions),
            }
        },
    )


def execute_tool_action(loop: Any, action: CodexAction) -> CodexActionResult:
    tool_name = str(action.metadata.get("tool_name") or "").strip()
    arguments = dict(action.metadata.get("arguments") or {})
    if not tool_name:
        return CodexActionResult(status="failed", final_output="missing tool_name")
    tool = loop.context.application_context.tools.get(tool_name)
    if tool is None:
        repaired = loop.context.application_context.tools.find_case_insensitive(tool_name)
        if repaired is not None:
            action.metadata["requested_tool_name"] = tool_name
            action.metadata["tool_name"] = repaired.name
            tool_name = repaired.name
            tool = repaired
        else:
            suggestions = loop.context.application_context.tools.suggest(tool_name)
            return CodexActionResult(
                status="failed",
                final_output=f"unknown tool: {tool_name}",
                metadata={
                    "error": {
                        "code": "TOOL_NOT_FOUND",
                        "message": f"unknown tool: {tool_name}",
                        "available_tools": loop.context.application_context.tools.names(),
                        "suggestions": suggestions,
                        "retriable": True,
                    }
                },
            )
    access_result = enforce_persona_tool_access(loop, action, tool, session=loop.context.session)
    if access_result is not None:
        return access_result
    try:
        result = execute_tool_call(tool, ToolCall(tool_name=tool_name, arguments=arguments), task=action, context=loop.context)
    except IsADirectoryError:
        recovered = recover_directory_tool_action(loop, action, tool_name, arguments)
        if recovered is not None:
            return recovered
        raise
    if not result.success:
        if isinstance(result.exception, IsADirectoryError):
            recovered = recover_directory_tool_action(loop, action, tool_name, arguments)
            if recovered is not None:
                return recovered
        if result.exception is not None:
            raise result.exception
        return CodexActionResult(
            status="failed",
            final_output=str(result.error or "tool execution failed"),
            metadata={"error": dict(result.metadata.get("error") or {})},
        )
    output = result.output
    if isinstance(output, dict):
        final_output = str(output.get("text") or output.get("content") or output.get("stdout") or output)
        artifacts = []
        if action.kind in {"apply_patch", "create_path", "edit_text", "move_path", "delete_path"}:
            artifacts.append(
                {
                    "artifact_type": "change_summary",
                    "title": tool_name,
                    "content": str(output.get("after_text") or output.get("text") or final_output),
                    "metadata": {"path": str(output.get("path") or "")},
                }
            )
        return CodexActionResult(status="completed", final_output=final_output, artifacts=artifacts, metadata={"tool_output": output})
    return CodexActionResult(status="completed", final_output=str(output or ""))


def recover_directory_tool_action(loop: Any, action: CodexAction, tool_name: str, arguments: dict[str, Any]) -> CodexActionResult | None:
    if tool_name not in {"read_workspace_text", "summarize_workspace_text"}:
        return None
    recovery_tool_name = "inspect_workspace_path" if "inspect_workspace_path" in loop.context.application_context.tools.names() else ""
    if not recovery_tool_name and "list_workspace_directory" in loop.context.application_context.tools.names():
        recovery_tool_name = "list_workspace_directory"
    if not recovery_tool_name:
        return None
    recovery_arguments = {
        "path": str(arguments.get("path") or ""),
        "use_last_focus": bool(arguments.get("use_last_focus")),
        "use_default_directory": bool(arguments.get("use_default_directory")),
    }
    recovery_tool = loop.context.application_context.tools.require(recovery_tool_name)
    result = execute_tool_call(recovery_tool, ToolCall(tool_name=recovery_tool_name, arguments=recovery_arguments), task=action, context=loop.context)
    if not result.success:
        if result.exception is not None:
            raise result.exception
        return CodexActionResult(status="failed", final_output=str(result.error or "tool execution failed"))
    action.metadata["requested_tool_name"] = tool_name
    action.metadata["tool_name"] = recovery_tool_name
    action.metadata["recovered_from_directory"] = True
    action.metadata["directory_recovery_source"] = tool_name
    output = dict(result.output or {})
    final_output = str(output.get("text") or output.get("content") or output.get("stdout") or output)
    metadata = {"tool_output": output, "directory_recovery": {"from_tool": tool_name, "to_tool": recovery_tool_name}}
    return CodexActionResult(status="completed", final_output=final_output, metadata=metadata)


def execute_verification_action(loop: Any, action: CodexAction) -> CodexActionResult:
    command = str(action.metadata.get("command") or action.instruction or "").strip()
    tool = loop.context.application_context.tools.require("run_shell_command")
    access_result = enforce_persona_tool_access(loop, action, tool, session=loop.context.session)
    if access_result is not None:
        return access_result
    result = execute_tool_call(tool, ToolCall(tool_name="run_shell_command", arguments={"command": command}), task=action, context=loop.context)
    if not result.success:
        return CodexActionResult(status="failed", final_output=str(result.error or "verification failed"))
    output = dict(result.output or {})
    success = bool(output.get("success"))
    summary = str(output.get("text") or output.get("stdout") or output.get("stderr") or "")
    return CodexActionResult(
        status="completed" if success else "failed",
        final_output=summary,
        artifacts=[{"artifact_type": "verification_log", "title": command, "content": summary, "metadata": {"command": command, "success": success}}],
        metadata={"verification": {"success": success, "summary": summary, "command": command}},
    )


def normalize_result(result: Any) -> CodexActionResult:
    if isinstance(result, CodexActionResult):
        return result
    if isinstance(result, dict):
        return CodexActionResult(
            status=str(result.get("status") or "completed"),
            final_output=str(result.get("final_output") or result.get("text") or ""),
            artifacts=list(result.get("artifacts") or []),
            artifact_ids=list(result.get("artifact_ids") or []),
            needs_approval=bool(result.get("needs_approval")),
            approval_reason=str(result.get("approval_reason") or ""),
            risk_class=str(result.get("risk_class") or ""),
            metadata=dict(result.get("metadata") or {}),
        )
    return CodexActionResult(status="completed", final_output=str(result or ""))


def enforce_persona_tool_access(loop: Any, action: CodexAction, tool: Any, *, session: Any) -> CodexActionResult | None:
    from agent_runtime_framework.agents.codex.personas import tool_access_for_persona

    persona = resolve_runtime_persona(loop.context, task=None, user_input=action.instruction)
    access = tool_access_for_persona(persona, tool)
    if session is not None:
        session.active_persona = persona.name
    action.metadata["runtime_persona"] = persona.name
    action.metadata["persona_tool_access"] = access
    tool_name = str(getattr(tool, "name", "") or action.metadata.get("tool_name") or "")
    if access == "deny":
        return CodexActionResult(
            status="failed",
            final_output=f"persona '{persona.name}' does not allow tool '{tool_name}'",
            metadata={"error": {"code": "PERSONA_TOOL_DENIED", "message": f"persona '{persona.name}' denied tool '{tool_name}'", "retriable": False}},
        )
    if access == "ask" and not bool(action.metadata.get("_approval_granted")):
        return CodexActionResult(
            status="pending",
            final_output=f"persona '{persona.name}' requires confirmation for tool '{tool_name}'",
            needs_approval=True,
            approval_reason=f"persona '{persona.name}' requires confirmation for tool '{tool_name}'",
            risk_class=action.risk_class or "high",
            metadata={"persona_tool_access": access, "runtime_persona": persona.name},
        )
    return None


def relative_workspace_path(loop: Any, path: str) -> str:
    if not path:
        return ""
    roots = getattr(loop.context.application_context.resource_repository, "allowed_roots", [])
    if not roots:
        return path.strip()
    root = Path(roots[0]).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except FileNotFoundError:
        resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return path.strip()
    return relative or "."
