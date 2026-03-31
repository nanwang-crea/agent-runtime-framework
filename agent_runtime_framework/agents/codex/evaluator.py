from __future__ import annotations

from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction, CodexEvaluationDecision, CodexTask


def evaluate_codex_output(task: CodexTask, session: Any, context: Any, tool_names: list[str]) -> CodexEvaluationDecision:
    if task.state.pending_verifications:
        return CodexEvaluationDecision(status="continue", summary="verification required before finish")
    last_action = _last_completed_action(task)
    if last_action is None:
        return CodexEvaluationDecision()
    if task.state.open_questions and last_action.kind == "respond":
        return CodexEvaluationDecision(status="continue", summary="cannot finish while open questions remain")
    if last_action.kind == "respond":
        return CodexEvaluationDecision(status="finish")
    return CodexEvaluationDecision(status="continue", summary="awaiting_summary")


def _last_completed_action(task: CodexTask) -> CodexAction | None:
    for action in reversed(task.actions):
        if action.status == "completed":
            return action
    return None
