from __future__ import annotations

from typing import Any

from agent_runtime_framework.agents.workspace_backend.models import WorkspaceAction, WorkspaceEvaluationDecision, WorkspaceTask


def evaluate_workspace_output(task: WorkspaceTask, session: Any, context: Any, tool_names: list[str]) -> WorkspaceEvaluationDecision:
    last_action = _last_completed_action(task)
    if last_action is None:
        return WorkspaceEvaluationDecision(status="continue", summary="no completed action")
    if last_action.kind == "respond":
        return WorkspaceEvaluationDecision(status="finish")
    return WorkspaceEvaluationDecision(status="continue", summary="awaiting_summary")


def _last_completed_action(task: WorkspaceTask) -> WorkspaceAction | None:
    for action in reversed(task.actions):
        if action.status == "completed":
            return action
    return None
