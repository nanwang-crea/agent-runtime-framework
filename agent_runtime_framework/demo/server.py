from __future__ import annotations

import argparse
import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent_runtime_framework.errors import AppError, log_app_error, normalize_app_error
from agent_runtime_framework.demo.app import DemoAssistantApp
from agent_runtime_framework.demo.bootstrap import create_demo_assistant_app
from agent_runtime_framework.demo.runtime_factory import DemoRuntimeFactory

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)

_FRONTEND_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend-shell" / "dist"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the desktop AI tool demo server.")
    parser.add_argument("--workspace", default=".", help="Workspace root that the assistant can access.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    args = parser.parse_args()

    app = create_demo_assistant_app(Path(args.workspace))
    server = ThreadingHTTPServer((args.host, args.port), _build_handler(app))
    print(f"Workspace Assistant running at http://{args.host}:{args.port}")
    print(f"Workspace: {app.workspace}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _build_handler(app: DemoAssistantApp) -> type[BaseHTTPRequestHandler]:
    class DemoHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            try:
                run_lifecycle = DemoRuntimeFactory(app).build_run_lifecycle()
                if self.path == "/api/session":
                    self._send_json(
                        {
                            "workspace": str(app.workspace),
                            "session": app.session_payload(),
                            "plan_history": app.plan_history_payload(),
                            "run_history": app.run_history_payload(),
                            "memory": app.memory_payload(),
                            "context": app.context_payload(),
                        }
                    )
                    return
                if self.path == "/api/model-center":
                    self._send_json(app.model_center.payload())
                    return
                asset_path = _resolve_frontend_path(self.path)
                if asset_path is not None:
                    self._send_file(asset_path)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._send_exception(exc, operation="GET")

        def do_POST(self) -> None:
            try:
                run_lifecycle = DemoRuntimeFactory(app).build_run_lifecycle()
                if self.path == "/api/chat":
                    payload = self._read_json()
                    message = str(payload.get("message") or "").strip()
                    logging.getLogger("demo.server").info("POST /api/chat message=%r", message[:80] if message else "")
                    if not message:
                        self._send_json({"error": "message is required"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    self._send_json(app.chat(message))
                    return
                if self.path == "/api/chat/stream":
                    payload = self._read_json()
                    message = str(payload.get("message") or "").strip()
                    logging.getLogger("demo.server").info("POST /api/chat/stream message=%r", message[:80] if message else "")
                    if not message:
                        self._send_json({"error": "message is required"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    self._send_event_stream(app.stream_chat(message))
                    return
                if self.path == "/api/approve":
                    payload = self._read_json()
                    token_id = str(payload.get("token_id") or "").strip()
                    approved = bool(payload.get("approved"))
                    if not token_id:
                        self._send_json({"error": "token_id is required"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    self._send_json(run_lifecycle.approve(token_id, approved))
                    return
                if self.path == "/api/replay":
                    payload = self._read_json()
                    run_id = str(payload.get("run_id") or "").strip()
                    if not run_id:
                        self._send_json({"error": "run_id is required"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    self._send_json(run_lifecycle.replay(run_id))
                    return
                if self.path == "/api/model-center":
                    payload = self._read_json()
                    self._send_json(app.model_center.update(payload))
                    return
                if self.path == "/api/context":
                    payload = self._read_json()
                    self._send_json(
                        app.switch_context(
                            agent_profile=str(payload.get("agent_profile") or "").strip() or None,
                            workspace=str(payload.get("workspace") or "").strip() or None,
                        )
                    )
                    return
                if self.path == "/api/model-center/actions":
                    payload = self._read_json()
                    action = str(payload.get("action") or "").strip()
                    self._send_json(app.model_center.run_action(action, payload))
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._send_exception(exc, operation="POST")

        def log_message(self, format: str, *args: Any) -> None:
            logging.getLogger("demo.server").info("%s - %s", self.address_string(), format % args)

        def _read_json(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length") or 0)
            if content_length <= 0:
                return {}
            raw = self.rfile.read(content_length)
            if not raw:
                return {}
            return dict(json.loads(raw.decode("utf-8")))

        def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path: Path) -> None:
            data = path.read_bytes()
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type = f"{content_type}; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_event_stream(self, events) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": stream-start\n\n")
            self.wfile.flush()
            try:
                for event in events:
                    event_name = str(event.get("type") or "message")
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"event: {event_name}\n".encode("utf-8"))
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except Exception as exc:
                logging.getLogger("demo.server").exception("stream request failed: %s", exc)
                payload = json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "code": "STREAM_BROKEN",
                            "message": "流式请求中断。",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "stage": "stream",
                            "retriable": True,
                            "suggestion": "可以重试一次；如果持续出现，请检查后端日志。",
                        },
                    },
                    ensure_ascii=False,
                )
                self.wfile.write(b"event: error\n")
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.close_connection = True

        def _send_exception(self, exc: Exception, *, operation: str) -> None:
            error = normalize_app_error(
                exc,
                code="DEMO_SERVER_ERROR",
                message="Demo server 处理请求时发生错误。",
                stage=operation.lower(),
                retriable=False,
            )
            log_app_error(logging.getLogger("demo.server"), error, exc=exc, event="http_handler_error")
            self._send_json({"error": error.as_dict()}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    return DemoHandler


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


def _load_asset(name: str) -> str:
    return (_frontend_dist_root() / name).read_text(encoding="utf-8")


if __name__ == "__main__":
    main()
