from __future__ import annotations

from pathlib import Path
from typing import Any


def build_codex_system_prompt(role_instruction: str) -> str:
    role = role_instruction.strip()
    sections = [
        "你是 Codex runtime agent。",
        "共享运行时规则：先理解任务语义，再根据资源语义、最近历史和可用工具决定下一步；不要把原始工具输出误当成最终回答；优先给出单一、可执行、证据充分的下一步。",
        "工具使用原则：优先使用工作区工具而不是泛化 shell；写操作要匹配 risk_class；在证据不足时继续收集，在证据充足时再综合回答。",
        role,
    ]
    return "\n".join(section for section in sections if section)


def build_tool_guidance_lines(context: Any, tool_names: list[str]) -> list[str]:
    lines: list[str] = []
    for name in tool_names:
        tool = context.application_context.tools.get(name)
        if tool is None:
            continue
        guidance = list(getattr(tool, "prompt_guidelines", []) or [])
        permission = str(getattr(tool, "permission_level", "") or "")
        if permission == "metadata_read":
            guidance.append("适合先做目标定位和结构确认，再决定是否深入读取。")
        elif permission == "content_read":
            guidance.append("适合在目标已明确后补足内容证据，不要直接把输出原样返回。")
        elif permission in {"safe_write", "destructive_write"}:
            guidance.append("只在目标明确且用户意图是修改时使用，随后尽量安排验证。")
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
            lines = [f"- {turn.role}: {turn.content}" for turn in recent_turns]
            sections.append("近期对话：\n" + "\n".join(lines))
    snapshot = context.application_context.session_memory.snapshot()
    if getattr(snapshot, "focused_resources", None):
        focus_lines = []
        for ref in snapshot.focused_resources[:3]:
            location = str(getattr(ref, "location", "") or "")
            try:
                label = Path(location).name or location
            except Exception:
                label = location
            focus_lines.append(f"- {label}: {location}")
        summary = str(getattr(snapshot, "last_summary", "") or "").strip()
        body = "最近焦点资源：\n" + "\n".join(focus_lines)
        if summary:
            body += f"\n最近焦点摘要：{summary}"
        sections.append(body)
    return "\n".join(sections)


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
        "资源语义：\n"
        f"- path: {semantics['path'] or '(unknown)'}\n"
        f"- resource_kind: {semantics['resource_kind'] or '(unknown)'}\n"
        f"- is_container: {str(bool(semantics['is_container'])).lower()}\n"
        f"- allowed_actions: {allowed}"
    )


def _risk_hint_for_permission(permission_level: str) -> str:
    return {
        "metadata_read": "low",
        "content_read": "low",
        "safe_write": "high",
        "destructive_write": "destructive",
    }.get(permission_level, "low")
