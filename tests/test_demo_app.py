from __future__ import annotations

from pathlib import Path

from agent_runtime_framework.demo import create_demo_assistant_app
from agent_runtime_framework.demo.server import _load_asset


def test_demo_assistant_app_returns_session_and_plan_history(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    payload = app.chat("读取 README.md")

    assert payload["status"] == "completed"
    assert payload["final_answer"] == "line one\nline two\nline three"
    assert payload["session"]["turns"][-1]["role"] == "assistant"
    assert payload["plan_history"]
    assert payload["plan_history"][-1]["steps"][-1]["status"] == "completed"


def test_demo_assistant_app_routes_normal_chat_to_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    payload = app.chat("你是谁？")

    assert payload["status"] == "completed"
    assert payload["capability_name"] == "conversation"
    assert "我现在已经支持正常对话" in payload["final_answer"]


def test_demo_assets_are_loadable():
    html = _load_asset("index.html")
    script = _load_asset("app.js")
    css = _load_asset("styles.css")

    assert "Desktop Assistant Demo" in html
    assert "fetchSession" in script
    assert ":root" in css
