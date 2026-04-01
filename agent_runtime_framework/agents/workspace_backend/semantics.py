from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime_framework.agents.workspace_backend.planner import infer_task_intent as _infer_task_intent


@dataclass(slots=True)
class TargetResolution:
    task_kind: str = "chat"
    target_hint: str = ""
    target_ref: str = ""
    scope_kind: str = "unknown"



def infer_task_intent(user_input: str, workspace_root: Path | None = None, context: Any | None = None, session: Any | None = None):
    return _infer_task_intent(user_input, workspace_root)



def resolve_task_intent(user_input: str, context: Any | None = None, session: Any | None = None):
    workspace_root = None
    application_context = getattr(context, "application_context", context)
    config = getattr(application_context, "config", {}) if application_context is not None else {}
    root_value = str((config or {}).get("default_directory") or "").strip()
    if root_value:
        workspace_root = Path(root_value).expanduser().resolve()
    return _infer_task_intent(user_input, workspace_root)



def build_task_intent_block(goal: str, workspace_root: str = "") -> str:
    intent = _infer_task_intent(goal, Path(workspace_root).expanduser().resolve() if workspace_root else None)
    return (
        "Task intent:\n"
        f"- task_kind: {intent.task_kind}\n"
        f"- user_intent: {intent.user_intent}\n"
        f"- target_hint: {intent.target_hint or '(none)'}"
    )



def goal_is_raw_read(goal: str) -> bool:
    lowered = str(goal or "").lower()
    return any(token in lowered for token in ("读取", "read", "打开", "open"))



def goal_prefers_summary(goal: str) -> bool:
    lowered = str(goal or "").lower()
    return any(token in lowered for token in ("总结", "概括", "summary", "summarize", "explain"))



def repository_target_hint(goal: str, workspace_root: Path | None = None) -> str:
    return _infer_task_intent(goal, workspace_root).target_hint
