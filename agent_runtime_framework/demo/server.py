from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from agent_runtime_framework.demo.app import DemoAssistantApp, create_demo_assistant_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the desktop assistant demo server.")
    parser.add_argument("--workspace", default=".", help="Workspace root that the assistant can access.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    args = parser.parse_args()

    app = create_demo_assistant_app(Path(args.workspace))
    server = ThreadingHTTPServer((args.host, args.port), _build_handler(app))
    print(f"Desktop assistant demo running at http://{args.host}:{args.port}")
    print(f"Workspace: {app.workspace}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _build_handler(app: DemoAssistantApp) -> type[BaseHTTPRequestHandler]:
    class DemoHandler(BaseHTTPRequestHandler):
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
                    }
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path == "/api/chat":
                payload = self._read_json()
                message = str(payload.get("message") or "").strip()
                if not message:
                    self._send_json({"error": "message is required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(app.chat(message))
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

    return DemoHandler


def _load_asset(name: str) -> str:
    return resources.files("agent_runtime_framework.demo.assets").joinpath(name).read_text(encoding="utf-8")


if __name__ == "__main__":
    main()
