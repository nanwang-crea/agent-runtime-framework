from __future__ import annotations

from pathlib import Path
import json

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
    assert payload["execution_trace"]
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


def test_demo_assistant_app_exposes_model_state_and_selection(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    before = app.models_payload()
    auth = app.authenticate_provider("openai", {"api_key": "test-key"})
    selected = app.select_model("conversation", "openai", "gpt-4.1-mini")

    assert before["providers"]
    assert auth["auth_session"]["authenticated"] is True
    assert selected["routes"]["conversation"]["model_name"] == "gpt-4.1-mini"


def test_demo_assistant_app_creates_default_config_center(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    app = create_demo_assistant_app(workspace)
    config = app.config_payload()

    assert config["providers"][0]["provider"] == "dashscope"
    assert config["routes"]["conversation"]["model_name"] == "qwen3.5-plus"
    assert (workspace / ".arf_demo_config.json").exists()


def test_demo_assistant_app_updates_config_and_persists_it(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    result = app.update_config(
        {
            "providers": {
                "dashscope": {
                    "api_key": "sk-test",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                }
            },
            "routes": {
                "conversation": {
                    "provider": "dashscope",
                    "model_name": "qwen-plus",
                }
            },
        }
    )

    persisted = json.loads((workspace / ".arf_demo_config.json").read_text(encoding="utf-8"))

    assert result["config"]["routes"]["conversation"]["model_name"] == "qwen-plus"
    assert result["models"]["routes"]["conversation"]["model_name"] == "qwen-plus"
    assert persisted["providers"]["dashscope"]["api_key"] == "sk-test"


def test_demo_assistant_app_streams_chat_events(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    events = list(app.stream_chat("你是谁？", chunk_size=8))

    assert events[0]["type"] == "start"
    assert any(event["type"] == "step" for event in events)
    assert any(event["type"] == "delta" for event in events)
    assert events[-1]["type"] == "final"
    assert events[-1]["payload"]["status"] == "completed"
