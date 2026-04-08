from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/session")
def get_session(request: Request):
    services = request.app.state.api_services
    return services.session.get_session()
