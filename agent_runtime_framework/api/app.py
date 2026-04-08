from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from agent_runtime_framework.api.bootstrap import create_api_services
from agent_runtime_framework.api.routes import (
    chat_routes,
    context_routes,
    model_center_routes,
    run_routes,
    session_routes,
)

_FRONTEND_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend-shell" / "dist"


def create_app(workspace: str | Path = ".") -> FastAPI:
    service_bundle = create_api_services(workspace)
    app = FastAPI()
    app.state.api_services = service_bundle
    app.include_router(session_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(context_routes.router)
    app.include_router(model_center_routes.router)
    app.include_router(run_routes.router)

    @app.get("/{path:path}")
    def get_frontend_asset(path: str):
        asset_path = _resolve_frontend_path(f"/{path}")
        if asset_path is None:
            raise HTTPException(status_code=404)
        return FileResponse(asset_path)

    return app


def _frontend_dist_root() -> Path:
    if not _FRONTEND_DIST_DIR.exists():
        raise FileNotFoundError(f"frontend dist not found: {_FRONTEND_DIST_DIR}")
    return _FRONTEND_DIST_DIR


def _resolve_frontend_path(request_path: str) -> Path | None:
    root = _frontend_dist_root()
    clean_path = request_path.split("?", 1)[0].split("#", 1)[0]
    if clean_path in {"", "/"}:
        return root / "index.html"
    relative = clean_path.lstrip("/")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    if "/api/" in clean_path or clean_path.startswith("/api"):
        return None
    return root / "index.html"
