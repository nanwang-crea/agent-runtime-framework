from __future__ import annotations

import re
from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.models import resolve_model_runtime
from agent_runtime_framework.runtime import parse_structured_output


def plan_codex_actions(user_input: str) -> list[CodexAction]:
    action = _plan_from_goal(user_input, tool_names=set())
    return [action] if action is not None else []


def plan_next_codex_action(task: Any, _session: Any, context: Any) -> CodexAction | None:
    completed = [action for action in task.actions if action.status == "completed"]
    if completed:
        last_action = completed[-1]
        if last_action.kind == "respond":
            return None
        if last_action.observation:
            return CodexAction(
                kind="respond",
                instruction=last_action.observation,
                metadata={"direct_output": True},
            )
        return None
    llm_planned = _plan_next_action_with_llm(task, context)
    if llm_planned is not None:
        return llm_planned
    raise AppError(
        code="MODEL_UNAVAILABLE",
        message="未配置可用的大模型，无法为 Codex Agent 规划下一步动作。",
        detail="planner model runtime is unavailable",
        stage="planner",
        retriable=True,
        suggestion="请先在前端“模型 / 配置”中配置并认证一个 planner 模型。",
    )


def _plan_from_goal(user_input: str, *, tool_names: set[str]) -> CodexAction | None:
    text = user_input.strip()
    lowered = text.lower()

    verification_prefixes = ("运行验证", "运行测试", "run verification", "verify ")
    for prefix in verification_prefixes:
        if lowered.startswith(prefix.lower()) and "run_shell_command" in tool_names:
            command = text[len(prefix) :].strip()
            if command:
                return CodexAction(
                    kind="run_verification",
                    instruction=command,
                    metadata={"command": command},
                )

    patch_match = re.search(
        r'把\s+([^\s]+)\s+里(?:的)?\s+"([^"]+)"\s+替换成\s+"([^"]+)"',
        text,
    )
    if patch_match and "apply_text_patch" in tool_names:
        path, search_text, replace_text = patch_match.groups()
        return CodexAction(
            kind="apply_patch",
            instruction=text,
            risk_class="high",
            metadata={
                "tool_name": "apply_text_patch",
                "arguments": {
                    "path": path,
                    "search_text": search_text,
                    "replace_text": replace_text,
                },
            },
        )

    create_match = re.search(r"(?:创建|新建)\s+([^\s]+)(?:\s+内容\s+(.+))?", text)
    if create_match and "create_workspace_path" in tool_names:
        path, content = create_match.groups()
        is_directory = any(marker in text for marker in ("文件夹", "目录", "folder", "directory"))
        return CodexAction(
            kind="create_path",
            instruction=text,
            risk_class="high",
            metadata={
                "tool_name": "create_workspace_path",
                "arguments": {
                    "path": path,
                    "content": (content or "").strip(),
                    "kind": "directory" if is_directory else "file",
                },
            },
        )

    edit_match = re.search(r"(?:编辑|修改)\s+([^\s]+)\s+内容\s+(.+)", text)
    if edit_match and "edit_workspace_text" in tool_names:
        path, content = edit_match.groups()
        return CodexAction(
            kind="edit_text",
            instruction=text,
            risk_class="high",
            metadata={
                "tool_name": "edit_workspace_text",
                "arguments": {"path": path, "content": content.strip()},
            },
        )

    move_match = re.search(r"把\s+([^\s]+)\s+(?:移动\s*到|重命名\s*到)\s+([^\s]+)", text)
    if move_match and "move_workspace_path" in tool_names:
        path, destination_path = move_match.groups()
        return CodexAction(
            kind="move_path",
            instruction=text,
            risk_class="high",
            metadata={
                "tool_name": "move_workspace_path",
                "arguments": {"path": path, "destination_path": destination_path},
            },
        )

    delete_match = re.search(r"(?:删除|delete)\s+([^\s]+)", text, flags=re.IGNORECASE)
    if delete_match and "delete_workspace_path" in tool_names:
        path = delete_match.group(1).strip()
        return CodexAction(
            kind="delete_path",
            instruction=text,
            risk_class="destructive",
            metadata={
                "tool_name": "delete_workspace_path",
                "arguments": {"path": path},
            },
        )

    summarize_match = re.search(r"(?:总结|概括|summarize)\s*([^\s]+)?", text, flags=re.IGNORECASE)
    if summarize_match and "summarize_workspace_text" in tool_names:
        path = (summarize_match.group(1) or "").strip()
        return CodexAction(
            kind="call_tool",
            instruction=text,
            metadata={
                "tool_name": "summarize_workspace_text",
                "arguments": {
                    "path": path,
                    "use_last_focus": any(marker in text for marker in ("刚才", "那个文件", "上一个")),
                },
            },
        )

    if any(marker in text for marker in ("列出", "列一下")) or lowered.startswith("list "):
        if "list_workspace_directory" in tool_names:
            path_match = re.search(r"(?:列出|列一下|list)\s*([^\s]+)?", text, flags=re.IGNORECASE)
            path = (path_match.group(1) or "").strip() if path_match else ""
            return CodexAction(
                kind="call_tool",
                instruction=text,
                metadata={
                    "tool_name": "list_workspace_directory",
                    "arguments": {
                        "path": path,
                        "use_last_focus": any(marker in text for marker in ("下面", "刚才", "上一个")),
                        "use_default_directory": not path,
                    },
                },
            )

    read_match = re.search(r"(?:读取|read)\s+([^\s]+)", text, flags=re.IGNORECASE)
    if read_match and "read_workspace_text" in tool_names:
        path = read_match.group(1).strip()
        return CodexAction(
            kind="call_tool",
            instruction=text,
            metadata={"tool_name": "read_workspace_text", "arguments": {"path": path}},
        )

    return CodexAction(kind="respond", instruction=text)


def _plan_next_action_with_llm(task: Any, context: Any) -> CodexAction | None:
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    tool_names = list(context.application_context.tools.names())
    if llm_client is None or not tool_names:
        return None
    tool_lines = []
    for name in tool_names:
        tool = context.application_context.tools.get(name)
        if tool is None:
            continue
        tool_lines.append(
            f"- name: {tool.name}; description: {tool.description}; input_schema: {tool.input_schema}; permission: {tool.permission_level}; risk_hint: {_risk_hint_for_permission(tool.permission_level)}"
        )
    action_lines = [
        f"- kind: {action.kind}; status: {action.status}; instruction: {action.instruction}; observation: {action.observation or ''}"
        for action in task.actions[-6:]
    ]
    planned = parse_structured_output(
        llm_client,
        model=model_name,
        system_prompt=(
            "你是 Codex 风格 agent 的 next-action planner。"
            "请根据任务目标、最近 observation 和可用工具，选择唯一的下一步动作。"
            "只输出合法 JSON，字段允许为 kind、instruction、tool_name、arguments、risk_class、direct_output。"
        ),
        user_prompt=(
            f"任务目标：{task.goal}\n"
            f"最近动作：\n{chr(10).join(action_lines) if action_lines else '(none)'}\n"
            f"可用工具：\n{chr(10).join(tool_lines)}\n"
            f"工作区根目录：{context.application_context.config.get('default_directory', '')}\n"
            "约束：\n"
            "- 只能选择可用工具列表中的 tool_name\n"
            "- 写操作必须设置合适的 risk_class\n"
            "- destructive_write 对应 destructive\n"
            "- safe_write 对应 high\n"
            "- content_read / metadata_read 对应 low\n"
        ),
        normalizer=lambda parsed: _normalize_llm_action(parsed),
        max_tokens=220,
    )
    return planned


def _normalize_llm_action(parsed: dict[str, Any]) -> CodexAction | None:
    if not isinstance(parsed, dict):
        return None
    kind = str(parsed.get("kind") or "").strip()
    if kind not in {"call_tool", "apply_patch", "move_path", "delete_path", "run_verification", "respond"}:
        return None
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
    return CodexAction(
        kind=kind,
        instruction=instruction or metadata["tool_name"],
        risk_class=risk_class,
        metadata=metadata,
    )


def _risk_hint_for_permission(permission_level: str) -> str:
    if permission_level == "destructive_write":
        return "destructive"
    if permission_level == "safe_write":
        return "high"
    return "low"
