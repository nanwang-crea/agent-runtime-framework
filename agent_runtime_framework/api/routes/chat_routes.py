from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()


def _event_stream(events):
    yield ": stream-start\n\n"
    for event in events:
        event_name = str(event.get("type") or "message")
        payload = __import__("json").dumps(event, ensure_ascii=False)
        yield f"event: {event_name}\n".encode("utf-8")
        yield f"data: {payload}\n\n".encode("utf-8")


@router.post("/api/chat")
def post_chat(payload: dict[str, Any], request: Request) -> Any:
    services = request.app.state.api_services
    message = str(payload.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)
    return services.chat.chat(message)


@router.post("/api/chat/stream")
def post_chat_stream(payload: dict[str, Any], request: Request) -> StreamingResponse:
    services = request.app.state.api_services
    message = str(payload.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)
    return StreamingResponse(
        _event_stream(services.chat.stream_chat(message)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "close",
            "X-Accel-Buffering": "no",
        },
    )
