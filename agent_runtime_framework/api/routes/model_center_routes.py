from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/model-center")
def get_model_center(request: Request) -> Any:
    services = request.app.state.api_services
    return services.model_center.payload()


@router.post("/api/model-center")
def post_model_center(payload: dict[str, Any], request: Request) -> Any:
    services = request.app.state.api_services
    return services.model_center.update(payload)


@router.post("/api/model-center/actions")
def post_model_center_actions(payload: dict[str, Any], request: Request) -> Any:
    services = request.app.state.api_services
    action = str(payload.get("action") or "").strip()
    return services.model_center.run_action(action, payload)
