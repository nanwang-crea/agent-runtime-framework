from __future__ import annotations

from typing import Any

from agent_runtime_framework.agents.codex.semantics import resolve_task_intent


def classify_task_profile(user_input: str, context: Any | None = None, session: Any | None = None) -> str:
    return resolve_task_intent(user_input, context, session=session).task_kind


def extract_workspace_target_hint(user_input: str) -> str:
    """Return empty string; target extraction is delegated to the LLM planner."""
    return ""


def is_list_only_request(goal: str) -> bool:
    """Deprecated: always returns False; list-vs-deep distinction handled by LLM."""
    return False
