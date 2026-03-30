from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from agent_runtime_framework.agents.codex.personas import RuntimePersona
from agent_runtime_framework.agents.codex.run_context import build_run_context_block
from agent_runtime_framework.agents.codex.semantics import build_task_intent_block as _build_task_intent_block


_PROMPTS_DIR = Path(__file__).with_name("prompts")


def build_codex_system_prompt(role_instruction: str, *, workflow_name: str = "", persona: RuntimePersona | None = None) -> str:
    role = role_instruction.strip()
    persona_prompt = persona.prompt_preamble if persona is not None else ""
    sections = [_load_prompt_doc("runtime_system"), persona_prompt, build_workflow_guidance(workflow_name), role]
    return "\n".join(section for section in sections if section)


def render_codex_prompt_doc(name: str, **values: Any) -> str:
    content = _load_prompt_doc(name)
    rendered = content
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def build_workflow_guidance(workflow_name: str) -> str:
    normalized = str(workflow_name or "").strip()
    if not normalized:
        return ""
    try:
        return _load_prompt_doc(normalized)
    except FileNotFoundError:
        return ""


def extract_json_block(text: str) -> str:
    stripped = text.strip()
    if "```" in stripped:
        stripped = re.sub(r"^.*?```(?:json)?\s*", "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"\s*```.*$", "", stripped, flags=re.DOTALL)
    return stripped.strip()


def build_tool_guidance_lines(context: Any, tool_names: list[str]) -> list[str]:
    lines: list[str] = []
    for name in tool_names:
        tool = context.application_context.tools.get(name)
        if tool is None:
            continue
        guidance = list(getattr(tool, "prompt_guidelines", []) or [])
        permission = str(getattr(tool, "permission_level", "") or "")
        if permission == "metadata_read":
            guidance.append("Good for target location and structure confirmation before deciding to read deeper.")
        elif permission == "content_read":
            guidance.append("Use after the target is confirmed; do not relay raw tool output directly.")
        elif permission in {"safe_write", "destructive_write"}:
            guidance.append("Use only when the target is clear and the user intends a change; schedule verification afterward.")
        lines.append(
            " | ".join(
                part
                for part in (
                    f"name: {tool.name}",
                    f"description: {tool.description}",
                    f"snippet: {tool.prompt_snippet}",
                    f"guidelines: {' / '.join(item for item in guidance if item)}",
                    f"input_schema: {tool.input_schema}",
                    f"permission: {permission}",
                    f"risk_hint: {_risk_hint_for_permission(permission)}",
                )
                if part
            )
        )
    return lines


def build_follow_up_context(*, session: Any | None, context: Any) -> str:
    sections: list[str] = []
    if session is not None:
        recent_turns = getattr(session, "turns", [])[-4:]
        if recent_turns:
            lines = []
            for turn in recent_turns:
                content = str(getattr(turn, "content", "") or "").strip().replace("\n", " ")
                if len(content) > 200:
                    content = content[:197].rstrip() + "..."
                lines.append(f"- {turn.role}: {content}")
            sections.append("Recent turns:\n" + "\n".join(lines))
    sections.append(build_run_context_block(context, session=session))
    return "\n".join(section for section in sections if section)


def extract_task_resource_semantics(task: Any) -> dict[str, Any]:
    plan = getattr(task, "plan", None)
    semantics = getattr(plan, "target_semantics", None)
    if semantics is not None:
        return {
            "path": str(getattr(semantics, "path", "") or ""),
            "resource_kind": str(getattr(semantics, "resource_kind", "") or ""),
            "is_container": bool(getattr(semantics, "is_container", False)),
            "allowed_actions": list(getattr(semantics, "allowed_actions", []) or []),
        }
    for action in reversed(getattr(task, "actions", [])):
        metadata = dict(getattr(action, "metadata", {}) or {})
        result = dict(metadata.get("result") or {})
        tool_output = dict(result.get("tool_output") or {})
        resource_kind = str(tool_output.get("resource_kind") or "").strip()
        if not resource_kind:
            continue
        return {
            "path": str(tool_output.get("resolved_path") or tool_output.get("path") or ""),
            "resource_kind": resource_kind,
            "is_container": bool(tool_output.get("is_container") or resource_kind == "directory"),
            "allowed_actions": [str(item) for item in tool_output.get("allowed_actions") or [] if str(item).strip()],
        }
    return {"path": "", "resource_kind": "", "is_container": False, "allowed_actions": []}


def build_resource_semantics_block(task: Any) -> str:
    semantics = extract_task_resource_semantics(task)
    allowed = ", ".join(semantics["allowed_actions"]) if semantics["allowed_actions"] else "(none)"
    return (
        "Resource semantics:\n"
        f"- path: {semantics['path'] or '(unknown)'}\n"
        f"- resource_kind: {semantics['resource_kind'] or '(unknown)'}\n"
        f"- is_container: {str(bool(semantics['is_container'])).lower()}\n"
        f"- allowed_actions: {allowed}"
    )


def build_task_intent_block(goal: str, workspace_root: str = "") -> str:
    root = Path(workspace_root) if workspace_root else None
    return _build_task_intent_block(goal, root)


def _risk_hint_for_permission(permission_level: str) -> str:
    return {
        "metadata_read": "low",
        "content_read": "low",
        "safe_write": "high",
        "destructive_write": "destructive",
    }.get(permission_level, "low")


@lru_cache(maxsize=16)
def _load_prompt_doc(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").strip()
