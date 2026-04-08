from __future__ import annotations

import json
from pathlib import Path


def test_frontend_shell_scaffold_contains_expected_files():
    root = Path(__file__).resolve().parents[1] / "frontend-shell"

    assert (root / "package.json").exists()
    assert (root / "src" / "App.tsx").exists()
    assert (root / "src" / "api.ts").exists()
    assert (root / "src" / "components" / "layout" / "Sidebar.tsx").exists()
    assert (root / "src" / "components" / "chat" / "ConversationView.tsx").exists()
    assert (root / "src" / "components" / "settings" / "SettingsView.tsx").exists()
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
    assert "runsByAnchor[userIndex]" in app_tsx
    assert "upsertRunCard" in app_tsx
    assert "ConversationView" in app_tsx


def test_frontend_shell_mentions_agent_and_workspace_switching():
    app_tsx = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "App.tsx").read_text(encoding="utf-8")
    sidebar_tsx = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    api_ts = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "api.ts").read_text(encoding="utf-8")

    assert "sidebar-footer-workspace" in sidebar_tsx
    assert "handleWorkspaceSwitch" in app_tsx
    assert "/api/context" in api_ts


def test_frontend_shell_uses_codex_like_two_column_shell():
    app_tsx = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "App.tsx").read_text(encoding="utf-8")
    sidebar_tsx = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    styles_css = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "styles.css").read_text(encoding="utf-8")

    assert "runsByAnchor" in app_tsx
    assert "Sidebar" in app_tsx
    assert "sidebar-shell" in sidebar_tsx
    assert ".codex-shell" in styles_css
    assert "ContextPanel" not in app_tsx


def test_frontend_shell_normalizes_non_string_trace_details():
    app_tsx = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "normalizeDetail(" in app_tsx
    assert "return normalizeDetail(lastTrace.detail);" in app_tsx
    assert "return detail ? `${step.name} · ${step.status} · ${detail}`" in app_tsx


def test_frontend_shell_keeps_pending_user_turn_until_final_payload():
    app_tsx = (Path(__file__).resolve().parents[1] / "frontend-shell" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "const finalPayload = await sendMessageStream(" in app_tsx
    assert "if (finalPayload !== null) {" in app_tsx
    assert 'setPendingUserMessage("");' in app_tsx


def test_frontend_model_center_supports_wire_api_configuration():
    root = Path(__file__).resolve().parents[1] / "frontend-shell" / "src"
    app_tsx = (root / "App.tsx").read_text(encoding="utf-8")
    types_ts = (root / "types.ts").read_text(encoding="utf-8")

    assert '["wire_api"]' in app_tsx
    assert 'connection: { base_url: draft.baseUrl, wire_api: draft.wireApi }' in app_tsx
    assert "wire_api: string;" in types_ts
