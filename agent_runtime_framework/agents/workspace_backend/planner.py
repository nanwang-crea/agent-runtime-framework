from __future__ import annotations

from pathlib import Path
from agent_runtime_framework.agents.workspace_backend.models import WorkspaceAction, WorkspaceTask, TaskIntent

_READ_HINTS = ("读取", "read", "查看", "看看", "讲解", "总结", "explain")
_DIR_HINTS = ("文件夹", "目录", "package", "职责", "结构", "folder", "工作区", "仓库")
_LIST_HINTS = ("列一下", "列出", "有什么", "list", "show")
_CHANGE_HINTS = ("修改", "重写", "删除", "新增", "创建", "edit", "rewrite", "delete")


def infer_task_intent(user_input: str, workspace_root: Path | None = None) -> TaskIntent:
    text = str(user_input or "").strip()
    lowered = text.lower()
    if any(token in text for token in ("你是谁", "what are you", "who are you")):
        return TaskIntent(task_kind="chat", user_intent="conversation")
    target_hint = _extract_path_hint(text, workspace_root)
    if any(token in text for token in _CHANGE_HINTS) or any(token in lowered for token in _CHANGE_HINTS):
        return TaskIntent(task_kind="change_and_verify", user_intent="workspace_change", target_hint=target_hint)
    if (any(token in text for token in _DIR_HINTS) or any(token in lowered for token in _DIR_HINTS) or (any(token in text for token in _LIST_HINTS) and any(token in text for token in ("工作区", "文件", "目录", "仓库")))):
        return TaskIntent(task_kind="repository_explainer", user_intent="repository_overview", target_hint=target_hint)
    if target_hint or any(token in text for token in _READ_HINTS) or any(token in lowered for token in _READ_HINTS):
        return TaskIntent(task_kind="file_reader", user_intent="workspace_read", target_hint=target_hint)
    return TaskIntent(task_kind="chat", user_intent="conversation")


def plan_workspace_actions(user_input: str) -> list[WorkspaceAction]:
    action = _plan_from_goal(user_input, tool_names=set())
    return [action] if action is not None else []



def _plan_from_goal(user_input: str, *, tool_names: set[str]) -> WorkspaceAction | None:
    intent = infer_task_intent(user_input)
    if intent.task_kind == "chat":
        return WorkspaceAction(kind="respond", instruction="我是当前 workspace backend 助手，可以继续帮你读文件、看目录、改代码和跑验证。", metadata={"direct_output": True})
    target_hint = intent.target_hint
    if intent.task_kind == "repository_explainer" and target_hint and "inspect_workspace_path" in tool_names:
        return WorkspaceAction(kind="call_tool", instruction=user_input, metadata={"tool_name": "inspect_workspace_path", "arguments": {"path": target_hint}})
    if "resolve_workspace_target" in tool_names:
        return WorkspaceAction(kind="call_tool", instruction=user_input, metadata={"tool_name": "resolve_workspace_target", "arguments": {"query": user_input, "target_hint": target_hint}})
    if target_hint and "read_workspace_text" in tool_names:
        return WorkspaceAction(kind="call_tool", instruction=user_input, metadata={"tool_name": "read_workspace_text", "arguments": {"path": target_hint}})
    return WorkspaceAction(kind="respond", instruction=user_input, metadata={"direct_output": True})


def _extract_path_hint(text: str, workspace_root: Path | None) -> str:
    stripped = text.strip().strip("`\"")
    candidates = [part.strip("`\"，。,. ") for part in text.replace("\n", " ").split()]
    ordered_candidates = []
    if stripped and " " not in stripped and "\n" not in stripped:
        ordered_candidates.append(stripped)
    ordered_candidates.extend(candidates)
    for candidate in ordered_candidates:
        if not candidate:
            continue
        if "/" in candidate or "." in Path(candidate).name:
            return candidate
        if workspace_root is not None and (workspace_root / candidate).exists():
            return candidate
    return ""
