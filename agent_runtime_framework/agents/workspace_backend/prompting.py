from __future__ import annotations

import re
from typing import Any

from agent_runtime_framework.agents.workspace_backend.run_context import build_run_context_block


_MINIMAL_PROMPTS = {
    "router_system": "你是路由器。只能输出 JSON，不要输出原因。可选结果只有 {\"route\":\"conversation\"} 或 {\"route\":\"codex\"}。",
    "router_user": "用户消息：{{user_input}}\n如果是普通寒暄或闲聊，输出 {\"route\":\"conversation\"}；如果涉及工作区、文件、目录、代码、测试、修改、读取、列出或总结，输出 {\"route\":\"codex\"}。不要输出原因。",
    "conversation_system": "你是一个简洁友好的中文助手。",
}


def build_workspace_system_prompt(role_instruction: str, *, workflow_name: str = "", persona: Any | None = None) -> str:
    sections = [str(getattr(persona, 'prompt_preamble', '') or '').strip(), str(role_instruction or '').strip()]
    if workflow_name:
        sections.append(f"Workflow: {workflow_name}")
    return "\n\n".join(section for section in sections if section)


def render_workspace_prompt_doc(name: str, **values: Any) -> str:
    template = _MINIMAL_PROMPTS.get(name, name)
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def build_workflow_guidance(workflow_name: str) -> str:
    return f"Workflow guidance: {workflow_name}" if workflow_name else ""


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
        lines.append(f"name: {tool.name} | description: {tool.description} | input_schema: {tool.input_schema}")
    return lines


def build_follow_up_context(*, session: Any | None, context: Any) -> str:
    sections: list[str] = []
    if session is not None:
        turns = getattr(session, "turns", [])[-4:]
        if turns:
            sections.append("Recent turns:\n" + "\n".join(f"- {turn.role}: {str(turn.content)[:160]}" for turn in turns))
    sections.append(build_run_context_block(context, session=session))
    return "\n\n".join(section for section in sections if section)


def extract_task_resource_semantics(task: Any) -> dict[str, Any]:
    state = getattr(task, "state", None)
    return {
        "path": str(getattr(state, "resolved_target", "") or ""),
        "resource_kind": "directory" if str(getattr(state, "resolved_target", "")).endswith("/") else "file",
        "is_container": False,
        "allowed_actions": [],
    }


def build_resource_semantics_block(task: Any) -> str:
    semantics = extract_task_resource_semantics(task)
    return (
        "Resource semantics:\n"
        f"- path: {semantics['path'] or '(unknown)'}\n"
        f"- resource_kind: {semantics['resource_kind'] or '(unknown)'}"
    )

