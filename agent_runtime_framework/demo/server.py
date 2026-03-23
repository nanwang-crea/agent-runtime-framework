from __future__ import annotations

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from agent_runtime_framework.demo.app import DemoAssistantApp, create_demo_assistant_app

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("agent_runtime_framework.assistant").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the desktop AI tool demo server.")
    parser.add_argument("--workspace", default=".", help="Workspace root that the assistant can access.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    args = parser.parse_args()

    app = create_demo_assistant_app(Path(args.workspace))
    server = ThreadingHTTPServer((args.host, args.port), _build_handler(app))
    print(f"Desktop AI tool running at http://{args.host}:{args.port}")
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
            if self.path == "/":
                self._send_text(_load_asset("index.html"), content_type="text/html; charset=utf-8")
                return
            if self.path == "/app.js":
                self._send_text(_load_asset("app.js"), content_type="application/javascript; charset=utf-8")
                return
            if self.path == "/styles.css":
                self._send_text(_load_asset("styles.css"), content_type="text/css; charset=utf-8")
                return
            if self.path == "/api/session":
                self._send_json(
                    {
                        "workspace": str(app.workspace),
                        "session": app.session_payload(),
                        "plan_history": app.plan_history_payload(),
                        "memory": app.memory_payload(),
                    }
                )
                return
            if self.path == "/api/model-center":
                self._send_json(app.model_center_payload())
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
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
                self._send_json(app.approve(token_id, approved))
                return
            if self.path == "/api/replay":
                payload = self._read_json()
                run_id = str(payload.get("run_id") or "").strip()
                if not run_id:
                    self._send_json({"error": "run_id is required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(app.replay(run_id))
                return
            if self.path == "/api/model-center":
                payload = self._read_json()
                self._send_json(app.update_model_center(payload))
                return
            if self.path == "/api/model-center/actions":
                payload = self._read_json()
                action = str(payload.get("action") or "").strip()
                if not action:
                    self._send_json({"error": "action is required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(app.run_model_center_action(action, payload))
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, content: str, *, content_type: str) -> None:
            data = content.encode("utf-8")
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

    return DemoHandler


def _load_asset(name: str) -> str:
    return resources.files("agent_runtime_framework.demo.assets").joinpath(name).read_text(encoding="utf-8")


if __name__ == "__main__":
    main()
