from __future__ import annotations

from typing import Any


def compact_text(value: str, *, limit: int = 200) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}...[已截断]"


def resource_payload(resource: Any) -> dict[str, Any]:
    return {
        "resource_id": str(getattr(resource, "resource_id", "")),
        "kind": str(getattr(resource, "kind", "")),
        "location": str(getattr(resource, "location", "")),
        "title": str(getattr(resource, "title", "")),
    }


def with_router_trace(decision: dict[str, str] | None, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    router_step = router_trace_step(decision)
    if router_step is None:
        return steps
    return [router_step, *steps]


def router_trace_step(decision: dict[str, str] | None) -> dict[str, Any] | None:
    if not decision:
        return None
    route = str(decision.get("route") or "").strip()
    source = str(decision.get("source") or "").strip()
    if not route:
        return None
    detail = f"route={route}"
    if source:
        detail = f"{detail}; source={source}"
    return {"name": "router", "status": "completed", "detail": detail}


def trace_detail_for_action(action: Any) -> str:
    base = str(action.observation or action.instruction or "")
    if not bool(action.metadata.get("from_evaluator")):
        return base
    source = str(action.metadata.get("evaluation_source") or "")
    reason = str(action.metadata.get("evaluator_reason") or "")
    detail = "decision=continue"
    if source:
        detail = f"{detail}; source={source}"
    if reason:
        detail = f"{detail}; reason={reason}"
    if base:
        detail = f"{detail}; payload={base}"
    return detail
