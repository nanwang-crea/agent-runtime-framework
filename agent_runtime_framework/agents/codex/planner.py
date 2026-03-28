from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction
from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import (
    build_codex_system_prompt,
    build_resource_semantics_block,
    build_tool_guidance_lines,
    extract_task_resource_semantics,
)
from agent_runtime_framework.agents.codex.run_context import available_tool_names, build_run_context_block
from agent_runtime_framework.agents.codex.workflows import workflow_name_for_task_profile
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime

logger = logging.getLogger(__name__)


def plan_codex_actions(user_input: str) -> list[CodexAction]:
    action = _plan_from_goal(user_input, tool_names=set())
    return [action] if action is not None else []


def plan_next_codex_action(task: Any, session: Any, context: Any) -> CodexAction | None:
    completed = [action for action in task.actions if action.status == "completed"]
    if not completed:
        deterministic = _plan_first_action_by_profile(task, context)
        if deterministic is not None:
            return deterministic
    if completed:
        last_action = completed[-1]
        if last_action.kind == "respond":
            return None
        profile = str(getattr(task, "task_profile", "") or "chat")
        tool_name = str(getattr(last_action, "metadata", {}).get("tool_name") or "")
        if last_action.observation and profile == "chat":
            return CodexAction(
                kind="respond",
                instruction=last_action.observation,
                subgoal="synthesize_answer",
                metadata={"direct_output": True},
            )
        if last_action.observation and profile == "file_reader" and tool_name in {"read_workspace_text", "read_workspace_excerpt", "summarize_workspace_text", "inspect_workspace_path"}:
            return CodexAction(
                kind="respond",
                instruction=_direct_file_reader_response(str(getattr(task, "goal", "") or ""), last_action.observation),
                subgoal="synthesize_answer",
                metadata={"direct_output": True},
            )
    llm_planned = _plan_next_action_with_llm(task, context, session=session)
    if llm_planned is not None:
        return llm_planned
    raise AppError(
        code="PLANNER_RUNTIME_MISSING",
        message="No LLM model configured; cannot plan the next action for the Codex Agent.",
        detail="planner model runtime is unavailable",
        stage="planner",
        retriable=True,
        suggestion="Configure and authenticate a planner model in the frontend settings first.",
    )


def _plan_first_action_by_profile(task: Any, context: Any) -> CodexAction | None:
    profile = str(getattr(task, "task_profile", "chat") or "chat")
    persona = resolve_runtime_persona(context, task=task)
    tool_names = set(available_tool_names(context, persona=persona))
    goal = str(getattr(task, "goal", "") or "")
    if profile in {"repository_explainer", "file_reader"} and "resolve_workspace_target" in tool_names:
        return CodexAction(
            kind="call_tool",
            instruction=goal,
            subgoal="gather_evidence",
            metadata={
                "tool_name": "resolve_workspace_target",
                "arguments": {"query": goal, "target_hint": ""},
            },
        )
    return None


def _plan_from_goal(user_input: str, *, tool_names: set[str]) -> CodexAction | None:
    return None


def _plan_next_action_with_llm(task: Any, context: Any, *, session: Any | None = None) -> CodexAction | None:
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    persona = resolve_runtime_persona(context, task=task)
    tool_names = available_tool_names(context, persona=persona)
    if llm_client is None or not tool_names:
        return None
    tool_lines = build_tool_guidance_lines(context, tool_names)
    action_lines = [
        f"- kind: {action.kind}; status: {action.status}; instruction: {action.instruction}; observation: {action.observation or ''}"
        for action in task.actions[-6:]
    ]
    system_prompt = build_codex_system_prompt(
        "You are the next-action planner. Based on the task goal, recent observations, resource semantics, history, and available tools, choose the single best next action. "
        "Output valid JSON only. Allowed fields: kind, instruction, tool_name, arguments, risk_class, direct_output. "
        "kind must be one of: call_tool, apply_patch, move_path, delete_path, run_verification, respond. "
        "Do not output other kinds like action, tool, task, or conversation."
        ,
        workflow_name=workflow_name_for_task_profile(str(getattr(task, "task_profile", "") or "")),
        persona=persona,
    )
    run_context_block = build_run_context_block(
        context,
        task=task,
        session=session,
        user_input=str(getattr(task, "goal", "") or ""),
        persona=persona,
    )
    semantics = extract_task_resource_semantics(task)
    preferred_file_tool = "summarize_workspace_text" if _goal_prefers_summary(str(getattr(task, "goal", "") or "")) else "read_workspace_text"
    user_prompt = (
        f"Goal: {task.goal}\n"
        f"Task profile: {getattr(task, 'task_profile', 'chat')}\n"
        f"Runtime persona: {persona.name}\n"
        f"{build_resource_semantics_block(task)}\n"
        f"{run_context_block}\n"
        f"Recent actions:\n{chr(10).join(action_lines) if action_lines else '(none)'}\n"
        f"Available tools:\n{chr(10).join(tool_lines)}\n"
        f"Workspace root: {context.application_context.config.get('default_directory', '')}\n"
        "Constraints:\n"
        "- tool_name must be from the available tools list\n"
        "- write operations must have an appropriate risk_class\n"
        "- destructive_write → destructive\n"
        "- safe_write → high\n"
        "- content_read / metadata_read → low\n"
        "- repository_explainer profile: resolve_workspace_target first, then inspect/read/list, then synthesize\n"
        f"- file_reader profile: resolve_workspace_target first; when resource_kind=file and allowed_actions={', '.join(semantics.get('allowed_actions') or []) or '(unknown)'}, prefer {preferred_file_tool}, then synthesize\n"
        "- change_and_verify profile: edit/patch/write first, then run verification, then summarize\n"
        "- chat profile: answer directly unless the user explicitly requests workspace inspection or code edits\n"
        f"- current persona evidence_threshold is {persona.evidence_threshold}; gather more evidence rather than finishing prematurely when evidence is insufficient\n"
        "Examples:\n"
        '- To resolve a workspace target: {"kind":"call_tool","tool_name":"resolve_workspace_target","arguments":{"query":"what is in the memory folder","target_hint":"memory"}}\n'
        '- To run a shell command: {"kind":"call_tool","tool_name":"run_shell_command","arguments":{"command":"pwd"},"risk_class":"high"}\n'
        '- To read README.md: {"kind":"call_tool","tool_name":"read_workspace_text","arguments":{"path":"README.md"},"risk_class":"low"}\n'
        '- To reply directly: {"kind":"respond","instruction":"..."}\n'
    )
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_prompt),
                ],
                temperature=0.0,
                max_tokens=600,
            ),
        )
    except Exception as exc:
        raise AppError(
            code="PLANNER_REQUEST_FAILED",
            message="Planner model request failed; cannot generate the next action.",
            detail=f"{type(exc).__name__}: {exc}",
            stage="planner",
            retriable=True,
            suggestion="Check the planner model configuration, authentication, and network connectivity.",
        ) from exc

    raw_content = (response.content or "").strip()
    try:
        parsed = json.loads(_extract_json_block(raw_content))
    except Exception as exc:
        logger.warning("planner invalid json: raw=%s", raw_content[:400])
        raise AppError(
            code="PLANNER_INVALID_JSON",
            message="Planner model returned a response but it is not valid JSON.",
            detail=raw_content[:400],
            stage="planner",
            retriable=True,
            suggestion="Check the planner prompt or switch to a more reliable model.",
        ) from exc

    invalid_reason = _invalid_action_reason(parsed, tool_names=set(tool_names))
    planned = None if invalid_reason else _normalize_llm_action(parsed, tool_names=set(tool_names))
    if planned is None:
        detail = f"reason={invalid_reason or 'unknown'}; parsed={json.dumps(parsed, ensure_ascii=False)[:400]}"
        logger.warning("planner normalization failed: %s", detail)
        raise AppError(
            code="PLANNER_NORMALIZATION_FAILED",
            message="Planner model returned JSON but the content does not satisfy the action constraints.",
            detail=detail,
            stage="planner",
            retriable=True,
            suggestion="Check that tool_name, kind, and arguments satisfy the constraints.",
        )
    return planned


def _extract_json_block(text: str) -> str:
    stripped = text.strip()
    if "```" in stripped:
        stripped = re.sub(r"^.*?```(?:json)?\s*", "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"\s*```.*$", "", stripped, flags=re.DOTALL)
    return stripped.strip()


def _goal_prefers_summary(goal: str) -> bool:
    return False


def _goal_is_raw_read(goal: str) -> bool:
    return False


def _direct_file_reader_response(goal: str, observation: str) -> str:
    return observation.strip()


def _normalize_llm_action(parsed: dict[str, Any], *, tool_names: set[str] | None = None) -> CodexAction | None:
    if _invalid_action_reason(parsed, tool_names=tool_names) is not None:
        return None
    kind = str(parsed.get("kind") or "").strip()
    instruction = str(parsed.get("instruction") or "").strip()
    metadata = {
        "tool_name": str(parsed.get("tool_name") or "").strip(),
        "arguments": dict(parsed.get("arguments") or {}),
    }
    if kind == "respond" and bool(parsed.get("direct_output")):
        metadata["direct_output"] = True
    if kind == "apply_patch" and not metadata["tool_name"]:
        metadata["tool_name"] = "apply_text_patch"
    if kind == "move_path" and not metadata["tool_name"]:
        metadata["tool_name"] = "move_workspace_path"
    if kind == "delete_path" and not metadata["tool_name"]:
        metadata["tool_name"] = "delete_workspace_path"
    if kind == "run_verification" and not instruction:
        instruction = str(metadata["arguments"].get("command") or "")
    if tool_names is not None and metadata["tool_name"] and metadata["tool_name"] not in tool_names:
        return None
    if kind != "respond" and not metadata["tool_name"] and kind != "run_verification":
        return None
    if not instruction and kind == "respond":
        return None
    tool_name = metadata.get("tool_name") or ""
    risk_class = str(parsed.get("risk_class") or "low")
    if tool_name == "delete_workspace_path":
        risk_class = "destructive"
    elif tool_name in {"apply_text_patch", "move_workspace_path", "create_workspace_path", "edit_workspace_text"}:
        risk_class = "high"
    subgoal = "execute_step"
    if kind == "respond":
        subgoal = "synthesize_answer"
    elif kind == "run_verification":
        subgoal = "verify_changes"
    elif kind in {"call_tool"}:
        subgoal = "gather_evidence"
    elif kind in {"apply_patch", "move_path", "delete_path"} or tool_name in {
        "apply_text_patch",
        "move_workspace_path",
        "create_workspace_path",
        "edit_workspace_text",
        "delete_workspace_path",
    }:
        subgoal = "modify_workspace"
    return CodexAction(
        kind=kind,
        instruction=instruction or metadata["tool_name"],
        subgoal=subgoal,
        risk_class=risk_class,
        metadata=metadata,
    )


def _invalid_action_reason(parsed: dict[str, Any], *, tool_names: set[str] | None = None) -> str | None:
    if not isinstance(parsed, dict):
        return "parsed payload is not an object"
    kind = str(parsed.get("kind") or "").strip()
    if kind not in {"call_tool", "apply_patch", "move_path", "delete_path", "run_verification", "respond"}:
        return f"unsupported kind '{kind or '<empty>'}'"
    tool_name = str(parsed.get("tool_name") or "").strip()
    if kind == "apply_patch" and not tool_name:
        tool_name = "apply_text_patch"
    if kind == "move_path" and not tool_name:
        tool_name = "move_workspace_path"
    if kind == "delete_path" and not tool_name:
        tool_name = "delete_workspace_path"
    if tool_names is not None and tool_name and tool_name not in tool_names:
        return f"tool_name '{tool_name}' is not in available tools"
    if kind != "respond" and not tool_name and kind != "run_verification":
        return "non-respond action is missing tool_name"
    instruction = str(parsed.get("instruction") or "").strip()
    arguments = dict(parsed.get("arguments") or {})
    if kind == "respond" and not instruction:
        return "respond action is missing instruction"
    if kind == "run_verification" and not instruction and not str(arguments.get("command") or "").strip():
        return "run_verification action is missing command"
    return None


def _risk_hint_for_permission(permission_level: str) -> str:
    if permission_level == "destructive_write":
        return "destructive"
    if permission_level == "safe_write":
        return "high"
    return "low"
