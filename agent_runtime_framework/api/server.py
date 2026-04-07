from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from agent_runtime_framework.api.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the desktop AI tool API server.")
    parser.add_argument("--workspace", default=".", help="Workspace root that the assistant can access.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    args = parser.parse_args()

    api = create_app(workspace=args.workspace)
    print(f"Workspace Assistant running at http://{args.host}:{args.port}")
    print(f"Workspace: {Path(args.workspace).expanduser().resolve()}")
    uvicorn.run(api, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
