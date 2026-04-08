from __future__ import annotations

from typing import Any

from agent_runtime_framework.api.process_trace import emit_process_event
from agent_runtime_framework.workflow.llm.access import chat_json
from agent_runtime_framework.workflow.llm.structured_output_repair import repair_structured_output_until_valid


def _normalize_clarification_resolution(resolved: dict[str, Any]) -> dict[str, Any]:
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


def _clarification_resolution_error(resolved: Any) -> str | None:
    if not isinstance(resolved, dict):
        return "clarification interpreter returned no structured payload"
    try:
        normalized = _normalize_clarification_resolution(resolved)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    if normalized["should_reask"]:
        return None
    if not normalized["confirmed_target"] and not normalized["preferred_path"]:
        return "clarification resolution missing preferred_path or confirmed_target"
    return None


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
    validation_error = _clarification_resolution_error(resolved)
    if validation_error is not None:
        def _record(event: dict[str, Any]) -> None:
            emit_process_event(
                getattr(context, "process_sink", None),
                {
                    "kind": "plan",
                    "status": "completed" if bool(event.get("success")) else "started",
                    "title": "内部修复澄清结果" if bool(event.get("success")) else "尝试修复澄清结果",
                    "detail": f"{str(event.get('contract_kind') or 'clarification_resolution')} · {int(event.get('attempts_used') or 0)} 次尝试",
                    "node_type": "repair",
                    "metadata": {"repair": True, **dict(event)},
                },
            )

        repaired = repair_structured_output_until_valid(
            context,
            role="planner",
            contract_kind="clarification_resolution",
            required_fields=["preferred_path", "confirmed_target", "updated_target_hints", "should_reask", "confidence", "reason"],
            original_output=resolved,
            request_payload=payload,
            validate=_clarification_resolution_error,
            extra_instructions=(
                "If should_reask is false, you must provide preferred_path or confirmed_target. "
                "If the target remains ambiguous, set should_reask true."
            ),
            on_record=_record,
        )
        if not isinstance(repaired, dict):
            raise RuntimeError(validation_error)
        resolved = repaired
    return _normalize_clarification_resolution(resolved)
