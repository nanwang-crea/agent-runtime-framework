from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/approve")
def post_approve(payload: dict[str, Any], request: Request) -> Any:
    services = request.app.state.api_services
    token_id = str(payload.get("token_id") or "").strip()
    approved = bool(payload.get("approved"))
    if not token_id:
        return JSONResponse({"error": "token_id is required"}, status_code=400)
    return services.runs.approve(token_id, approved)


@router.post("/api/replay")
def post_replay(payload: dict[str, Any], request: Request) -> Any:
    services = request.app.state.api_services
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return JSONResponse({"error": "run_id is required"}, status_code=400)
    return services.runs.replay(run_id)
