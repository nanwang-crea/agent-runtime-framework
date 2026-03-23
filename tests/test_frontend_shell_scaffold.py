from __future__ import annotations

import json
from pathlib import Path


def test_frontend_shell_scaffold_contains_expected_files():
    root = Path(__file__).resolve().parents[1] / "frontend-shell"

    assert (root / "package.json").exists()
    assert (root / "src" / "App.tsx").exists()
    assert (root / "src" / "api.ts").exists()
    assert (root / "electron" / "main.cjs").exists()
    assert (root / "electron" / "preload.cjs").exists()


def test_frontend_shell_package_declares_vite_react_and_electron():
    package_json = Path(__file__).resolve().parents[1] / "frontend-shell" / "package.json"
    payload = json.loads(package_json.read_text(encoding="utf-8"))

    assert payload["scripts"]["dev"] == 'concurrently -k "npm:dev:web" "npm:dev:electron"'
    assert payload["scripts"]["dev:web"] == "vite --host 127.0.0.1 --strictPort"
    assert payload["scripts"]["dev:electron"] == "wait-on http-get://127.0.0.1:3000 && electron ."
    assert payload["scripts"]["start"] == "electron ."
    assert "@vitejs/plugin-react" in payload["devDependencies"]
    assert "electron" in payload["devDependencies"]
    assert "wait-on" in payload["devDependencies"]
    assert "react" in payload["dependencies"]
    assert "react-markdown" in payload["dependencies"]
    assert "remark-gfm" in payload["dependencies"]


def test_frontend_chat_streaming_keeps_run_card_separate_from_answer_body():
    app_tsx = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "streamingReply" in app_tsx
    assert "run.reply" not in app_tsx
    assert "run-draft" not in app_tsx
    assert "messagesRef.current.scrollTop = messagesRef.current.scrollHeight" in app_tsx
    assert "anchorUserTurnIndex" in app_tsx
    assert "run.anchorUserTurnIndex === userIndex" in app_tsx
    assert "upsertRunCard" in app_tsx
    assert 'phaseLabel: "请求已发送"' not in app_tsx


def test_frontend_shell_mentions_agent_and_workspace_switching():
    app_tsx = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "App.tsx").read_text(encoding="utf-8")
    api_ts = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "api.ts").read_text(encoding="utf-8")

    assert "Agent" in app_tsx
    assert "Workspace" in app_tsx
    assert "/api/context" in api_ts
