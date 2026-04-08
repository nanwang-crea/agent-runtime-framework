from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.llm.access import chat_json


def resolve_clarification_response(
    context: Any,
    *,
    prior_goal_envelope: dict[str, Any],
    pending_request: dict[str, Any],
    user_response: str,
    prior_state: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = {
        "original_goal": str(prior_goal_envelope.get("goal") or ""),
        "target_hints": list(prior_goal_envelope.get("target_hints") or []),
        "pending_request": dict(pending_request or {}),
        "user_response": str(user_response or "").strip(),
        "memory_state": dict((prior_state or {}).get("memory_state") or {}),
    }
    resolved = chat_json(
        context,
        role="planner",
        system_prompt=(
            "Resolve a clarification response for an existing workflow run. "
            "Return JSON only with keys: preferred_path, confirmed_target, excluded_targets, "
            "updated_target_hints, should_reask, confidence, reason."
        ),
        payload=payload,
        max_tokens=300,
    )
    if not isinstance(resolved, dict):
        raise RuntimeError("clarification interpreter returned no structured payload")
    preferred_path = str(resolved.get("preferred_path") or resolved.get("confirmed_target") or "").strip()
    confirmed_target = str(resolved.get("confirmed_target") or preferred_path).strip()
    confirmed = bool(resolved.get("confirmed"))
    if not confirmed and confirmed_target and not bool(resolved.get("should_reask")):
        confirmed = True
    return {
        "preferred_path": preferred_path,
        "confirmed_target": confirmed_target,
        "confirmed": confirmed,
        "excluded_targets": [str(item) for item in resolved.get("excluded_targets", []) or [] if str(item).strip()],
        "updated_target_hints": [str(item) for item in resolved.get("updated_target_hints", []) or [] if str(item).strip()] or ([preferred_path] if preferred_path else []),
        "should_reask": bool(resolved.get("should_reask")),
        "confidence": float(resolved.get("confidence") or 0.8),
        "reason": str(resolved.get("reason") or "clarification resolved").strip() or "clarification resolved",
    }
