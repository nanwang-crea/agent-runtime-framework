from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace

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
    assert "我可以继续和你对话" in payload["final_answer"]
    assert payload["execution_trace"]
    assert payload["execution_trace"][-1]["name"] == "conversation"
    assert "source=fallback" in str(payload["execution_trace"][-1]["detail"])


def test_demo_assets_are_loadable():
    html = _load_asset("index.html")
    script = _load_asset("app.js")
    css = _load_asset("styles.css")

    assert "Desktop Assistant Demo" in html
    assert "fetchSession" in script
    assert ":root" in css


def test_demo_assistant_app_updates_model_center_auth_and_routing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    before = app.model_center_payload()
    app.update_model_center(
        {
            "instances": {
                "openai": {
                    "type": "openai_compatible",
                    "credentials": {"api_key": "test-key"},
                    "connection": {"base_url": "https://api.openai.com/v1"},
                }
            }
        }
    )
    auth_payload = app.run_model_center_action("authenticate_instance", {"instance": "openai"})
    selected = app.update_model_center(
        {
            "routes": {
                "conversation": {"instance": "openai", "model": "gpt-5.4"},
            }
        }
    )

    assert before["runtime"]["instances"]
    assert auth_payload["runtime"]["instances"]["openai"]["authenticated"] is True
    assert selected["config"]["routes"]["conversation"]["model"] == "gpt-5.4"


def test_demo_assistant_app_exposes_minimax_and_codex_models(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    payload = app.model_center_payload()
    by_instance = payload["runtime"]["instances"]

    assert "minimax" in by_instance
    assert "codex_local" in by_instance
    assert any(model["model_name"] == "MiniMax-M2.1" for model in by_instance["minimax"]["models"])
    assert any(model["model_name"] == "gpt-5.3-codex" for model in by_instance["codex_local"]["models"])


def test_demo_assistant_app_exposes_model_center_payload(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    payload = app.model_center_payload()

    assert payload["config"]["schema_version"] == 3
    assert "runtime" in payload
    assert "dashscope" in payload["config"]["instances"]
    assert any(model["model_name"] == "qwen3.5-plus" for model in payload["runtime"]["instances"]["dashscope"]["models"])


def test_demo_assistant_app_updates_model_center_routes(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    payload = app.update_model_center(
        {
            "routes": {
                "conversation": {"instance": "dashscope", "model": "qwen-plus"},
                "capability_selector": {"instance": "dashscope", "model": "qwen-plus"},
                "planner": {"instance": "dashscope", "model": "qwen-plus"},
            }
        }
    )

    assert payload["config"]["routes"]["conversation"]["model"] == "qwen-plus"
    persisted = json.loads((workspace / ".arf_demo_config.json").read_text(encoding="utf-8"))
    assert persisted["routes"]["conversation"]["model"] == "qwen-plus"


def test_demo_assistant_app_default_route_can_drive_other_roles(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    payload = app.update_model_center(
        {
            "routes": {
                "default": {"instance": "minimax", "model": "MiniMax-M2.1"},
            }
        }
    )

    assert payload["config"]["routes"]["default"] == {"instance": "minimax", "model": "MiniMax-M2.1"}
    assert payload["runtime"]["routes"]["default"] == {"instance": "minimax", "model": "MiniMax-M2.1"}


def test_demo_assistant_app_creates_default_config_center(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    app = create_demo_assistant_app(workspace)
    config = app.model_center_payload()["config"]

    assert "dashscope" in config["instances"]
    assert config["routes"]["conversation"]["model"] == "qwen3.5-plus"
    assert (workspace / ".arf_demo_config.json").exists()


def test_demo_assistant_app_updates_config_and_persists_it(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    result = app.update_model_center(
        {
            "instances": {
                "dashscope": {
                    "type": "openai_compatible",
                    "credentials": {"api_key": "sk-test"},
                    "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
                }
            },
            "routes": {
                "conversation": {
                    "instance": "dashscope",
                    "model": "qwen-plus",
                }
            },
        }
    )

    persisted = json.loads((workspace / ".arf_demo_config.json").read_text(encoding="utf-8"))

    assert result["config"]["routes"]["conversation"]["model"] == "qwen-plus"
    assert persisted["schema_version"] == 3
    assert persisted["instances"]["dashscope"]["credentials"]["api_key"] == "sk-test"


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


def test_demo_assistant_app_emits_single_delta_for_fallback_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    events = list(app.stream_chat("你现在是跟我流式输出嘛？", chunk_size=6))
    delta_events = [event for event in events if event["type"] == "delta"]

    assert len(delta_events) == 1
    assert "".join(event["delta"] for event in delta_events) == events[-1]["payload"]["final_answer"]


def test_demo_assistant_app_emits_single_delta_for_non_conversation_results(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    events = list(app.stream_chat("读取 README.md", chunk_size=4))
    delta_events = [event for event in events if event["type"] == "delta"]

    assert len(delta_events) == 1
    assert delta_events[0]["delta"] == "line one\nline two\nline three"


def test_demo_assistant_app_emits_structured_error_for_directory_summarize(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs_dir = workspace / "docs"
    docs_dir.mkdir()
    app = create_demo_assistant_app(workspace)

    events = list(app.stream_chat("总结 docs"))

    error_events = [event for event in events if event["type"] == "error"]

    assert error_events
    assert error_events[-1]["error"]["code"] == "RESOURCE_IS_DIRECTORY"
    assert "目标是目录" in error_events[-1]["error"]["message"]
    assert error_events[-1]["error"]["retriable"] is True
    assert events[-1]["type"] == "error"


def test_demo_assistant_app_emits_memory_event_after_successful_desktop_action(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readme = workspace / "README.md"
    readme.write_text("line one\nline two\nline three", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    events = list(app.stream_chat("读取 README.md"))

    memory_events = [event for event in events if event["type"] == "memory"]

    assert memory_events
    assert memory_events[-1]["memory"]["focused_resource"]["title"] == "README.md"
    assert "line one" in str(memory_events[-1]["memory"]["last_summary"])


class _StreamingCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter(
                [
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你好"))]),
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="，我是流式回复"))]),
                ]
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="你好，我是非流式回复"))]
        )


class _StreamingLLM:
    def __init__(self) -> None:
        self.completions = _StreamingCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_demo_assistant_app_uses_llm_streaming_for_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)
    app.context.application_context.llm_client = _StreamingLLM()
    app.context.application_context.llm_model = "test-model"

    events = list(app.stream_chat("你好"))

    delta_events = [event for event in events if event["type"] == "delta"]

    assert [event["delta"] for event in delta_events] == ["你好", "，我是流式回复"]
    assert events[-1]["payload"]["final_answer"] == "你好，我是流式回复"
    assert "source=model" in str(events[-1]["payload"]["execution_trace"][-1]["detail"])
    assert any(call.get("stream") is True for call in app.context.application_context.llm_client.completions.calls)
