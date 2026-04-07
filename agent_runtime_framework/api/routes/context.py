from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/api/context")
def post_context(payload: dict[str, Any], request: Request) -> Any:
    services = request.app.state.api_services
    return services.context.switch_context(
        agent_profile=str(payload.get("agent_profile") or "").strip() or None,
        workspace=str(payload.get("workspace") or "").strip() or None,
    )
