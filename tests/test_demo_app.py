from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace

from agent_runtime_framework.agents.codex.models import CodexAction
from agent_runtime_framework.models import AuthSession, ModelProfile
from agent_runtime_framework.agents.codex.planner import _plan_from_goal
from agent_runtime_framework.demo import create_demo_assistant_app
from agent_runtime_framework.demo.server import _load_asset


def _create_demo_assistant_app_with_test_planner(workspace: Path):
    app = create_demo_assistant_app(workspace)
    def _planner(task, _session, _context, tool_names):
        completed = [action for action in task.actions if action.status == "completed"]
        if completed:
            last_action = completed[-1]
            if last_action.kind == "respond":
                return None
            if last_action.observation:
                return CodexAction(
                    kind="respond",
                    instruction=last_action.observation,
                    metadata={"direct_output": True},
                )
            return None
        return _plan_from_goal(task.goal, tool_names=set(tool_names))

    app.context.services["next_action_planner"] = _planner
    return app


def test_demo_assistant_app_returns_session_and_plan_history(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.chat("读取 README.md")

    assert payload["status"] == "completed"
    assert payload["final_answer"] == "line one\nline two\nline three"
    assert payload["session"]["turns"][-1]["role"] == "assistant"
    assert payload["plan_history"]
    assert payload["execution_trace"]
    assert payload["plan_history"][-1]["steps"][-1]["status"] == "completed"
    assert payload["plan_history"][-1]["steps"][0]["capability_name"] == "call_tool"


def test_demo_assistant_app_can_replay_run_by_run_id(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    first = app.chat("读取 README.md")
    replayed = app.replay(first["run_id"])

    assert replayed["status"] == "completed"
    assert replayed["final_answer"] == "line one\nline two\nline three"


def test_demo_assistant_app_routes_normal_chat_to_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.chat("你是谁？")

    assert payload["status"] == "completed"
    assert payload["capability_name"] == "conversation"
    assert "我可以继续和你对话" in payload["final_answer"]
    assert payload["execution_trace"]
    assert payload["execution_trace"][0]["name"] == "router"
    assert "conversation" in str(payload["execution_trace"][0]["detail"])
    assert payload["execution_trace"][-1]["name"] == "respond"


def test_demo_assets_are_loadable():
    html = _load_asset("index.html")
    script = _load_asset("app.js")
    css = _load_asset("styles.css")

    assert "桌面端 AI 工具" in html
    assert "fetchSession" in script
    assert ":root" in css


def test_demo_assistant_app_updates_model_center_auth_and_routing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

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
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.model_center_payload()
    by_instance = payload["runtime"]["instances"]

    assert "minimax" in by_instance
    assert "codex_local" in by_instance
    assert any(model["model_name"] == "MiniMax-M2.1" for model in by_instance["minimax"]["models"])
    assert any(model["model_name"] == "gpt-5.3-codex" for model in by_instance["codex_local"]["models"])


def test_demo_assistant_app_exposes_model_center_payload(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.model_center_payload()

    assert payload["config"]["schema_version"] == 3
    assert "runtime" in payload
    assert "dashscope" in payload["config"]["instances"]
    assert any(model["model_name"] == "qwen3.5-plus" for model in payload["runtime"]["instances"]["dashscope"]["models"])


def test_demo_assistant_app_updates_model_center_routes(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.update_model_center(
        {
            "routes": {
                "conversation": {"instance": "dashscope", "model": "qwen-plus"},
                "router": {"instance": "dashscope", "model": "qwen-plus"},
                "capability_selector": {"instance": "dashscope", "model": "qwen-plus"},
                "planner": {"instance": "dashscope", "model": "qwen-plus"},
            }
        }
    )

    assert payload["config"]["routes"]["conversation"]["model"] == "qwen-plus"
    assert payload["config"]["routes"]["router"]["model"] == "qwen-plus"
    persisted = json.loads((workspace / ".arf_demo_config.json").read_text(encoding="utf-8"))
    assert persisted["routes"]["conversation"]["model"] == "qwen-plus"
    assert persisted["routes"]["router"]["model"] == "qwen-plus"


def test_demo_assistant_app_default_route_can_drive_other_roles(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

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

    app = _create_demo_assistant_app_with_test_planner(workspace)
    config = app.model_center_payload()["config"]

    assert "dashscope" in config["instances"]
    assert config["routes"]["conversation"]["model"] == "qwen3.5-plus"
    assert (workspace / ".arf_demo_config.json").exists()


def test_demo_assistant_app_updates_config_and_persists_it(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

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
    app = _create_demo_assistant_app_with_test_planner(workspace)

    events = list(app.stream_chat("你是谁？", chunk_size=8))

    assert events[0]["type"] == "start"
    assert any(event["type"] == "step" for event in events)
    assert any(event["type"] == "delta" for event in events)
    assert events[-1]["type"] == "final"
    assert events[-1]["payload"]["status"] == "completed"


def test_demo_assistant_app_stream_final_payload_includes_context(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    events = list(app.stream_chat("你是谁？", chunk_size=8))
    payload = events[-1]["payload"]

    assert payload["context"]["active_agent"] in {"codex", "qa_only"}
    assert payload["context"]["active_workspace"] == str(workspace)
    assert payload["context"]["available_agents"]


def test_demo_assistant_app_emits_single_delta_for_fallback_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    events = list(app.stream_chat("你现在是跟我流式输出嘛？", chunk_size=6))
    delta_events = [event for event in events if event["type"] == "delta"]

    assert len(delta_events) == 1
    assert "".join(event["delta"] for event in delta_events) == events[-1]["payload"]["final_answer"]


def test_demo_assistant_app_emits_single_delta_for_non_conversation_results(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    events = list(app.stream_chat("读取 README.md", chunk_size=4))
    delta_events = [event for event in events if event["type"] == "delta"]

    assert len(delta_events) == 1
    assert delta_events[0]["delta"] == "line one\nline two\nline three"


def test_demo_assistant_app_emits_structured_error_for_directory_summarize(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs_dir = workspace / "docs"
    docs_dir.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    events = list(app.stream_chat("总结 docs"))

    error_events = [event for event in events if event["type"] == "error"]

    assert error_events
    assert error_events[-1]["error"]["code"] == "RESOURCE_IS_DIRECTORY"
    assert "目标是目录" in error_events[-1]["error"]["message"]
    assert error_events[-1]["error"]["retriable"] is True
    assert events[-1]["type"] == "error"


def test_demo_assistant_app_requires_llm_for_codex_agent_planning(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    payload = app.chat("列一下当前工作区都有什么文件")

    assert payload["status"] == "error"
    assert payload["error"]["code"] == "MODEL_UNAVAILABLE"
    assert payload["error"]["stage"] == "planner"


def test_demo_assistant_app_routes_plain_greeting_without_planner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    def _unexpected_planner(*_args, **_kwargs):
        raise AssertionError("planner should not be called for plain conversation")

    app.context.services["next_action_planner"] = _unexpected_planner

    payload = app.chat("你好")

    assert payload["status"] == "completed"
    assert payload["capability_name"] == "conversation"
    assert payload["execution_trace"][-1]["name"] == "respond"


def test_demo_assistant_app_stream_routes_plain_greeting_without_planner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    def _unexpected_planner(*_args, **_kwargs):
        raise AssertionError("planner should not be called for plain conversation")

    app.context.services["next_action_planner"] = _unexpected_planner

    events = list(app.stream_chat("你好"))

    assert events[-1]["type"] == "final"
    assert events[-1]["payload"]["status"] == "completed"
    assert events[-1]["payload"]["capability_name"] == "conversation"


def test_demo_assistant_app_uses_router_role_before_planner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    class _RouterClient:
        def create_chat_completion(self, _request):
            return SimpleNamespace(content='{"route":"conversation","reason":"user asked to discuss before acting"}')

    class _RouterInstance:
        instance_id = "router_fake"

        def list_models(self):
            return [
                ModelProfile(
                    instance=self.instance_id,
                    model_name="router-model",
                    display_name="Router Model",
                    recommended_roles=["router"],
                )
            ]

        def authenticate(self, credentials, store):
            if credentials.get("api_key"):
                store.set(self.instance_id, {"api_key": credentials["api_key"]})
            return AuthSession(instance=self.instance_id, authenticated=True, auth_type="api_key")

        def get_client(self, _store):
            return _RouterClient()

    app.model_registry.register_instance(_RouterInstance())
    app.model_registry.authenticate("router_fake", {"api_key": "secret"})
    app.model_router.set_route("router", instance_id="router_fake", model_name="router-model")

    def _unexpected_planner(*_args, **_kwargs):
        raise AssertionError("planner should not be called when router chooses conversation")

    app.context.services["next_action_planner"] = _unexpected_planner

    payload = app.chat("读取 README.md")

    assert payload["status"] == "completed"
    assert payload["capability_name"] == "conversation"
    assert payload["execution_trace"][0]["name"] == "router"
    assert "source=model" in str(payload["execution_trace"][0]["detail"])


def test_demo_assistant_app_stream_returns_model_unavailable_without_final_payload(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    events = list(app.stream_chat("列一下当前工作区都有什么文件"))

    assert [event["type"] for event in events][-1] == "error"
    assert events[-1]["error"]["code"] == "MODEL_UNAVAILABLE"


def test_demo_assistant_app_emits_memory_event_after_successful_desktop_action(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readme = workspace / "README.md"
    readme.write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    events = list(app.stream_chat("读取 README.md"))

    memory_events = [event for event in events if event["type"] == "memory"]

    assert memory_events
    assert memory_events[-1]["memory"]["focused_resource"]["title"] == "README.md"
    assert "line one" in str(memory_events[-1]["memory"]["last_summary"])


def test_demo_assistant_app_uses_codex_loop_for_workspace_actions(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.chat("读取 README.md")

    assert payload["status"] == "completed"
    assert payload["execution_trace"][0]["name"] == "router"
    assert "codex" in str(payload["execution_trace"][0]["detail"])
    assert payload["execution_trace"][1]["name"] == "call_tool"
    assert payload["execution_trace"][-1]["name"] == "respond"


def test_demo_assistant_app_compacts_large_trace_and_plan_details(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    large_text = "A" * 8000
    (workspace / "README.md").write_text(large_text, encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.chat("读取 README.md")

    assert payload["final_answer"] == large_text
    assert len(str(payload["execution_trace"][0]["detail"])) < 400
    assert len(str(payload["plan_history"][-1]["steps"][0]["observation"])) < 400


def test_demo_assistant_app_can_switch_agent_profile_within_session(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.switch_context(agent_profile="qa_only")

    assert payload["context"]["active_agent"] == "qa_only"
    assert any(profile["id"] == "codex" for profile in payload["context"]["available_agents"])
    assert any(profile["id"] == "qa_only" for profile in payload["context"]["available_agents"])


def test_demo_assistant_app_can_switch_workspace_within_session(tmp_path: Path):
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    (other / "README.md").write_text("from other workspace", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.switch_context(workspace=str(other))
    chat = app.chat("读取 README.md")

    assert payload["context"]["active_workspace"] == str(other)
    assert str(other) in payload["context"]["available_workspaces"]
    assert chat["final_answer"] == "from other workspace"


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
    app.switch_context(agent_profile="qa_only")
    app.context.application_context.llm_client = _StreamingLLM()
    app.context.application_context.llm_model = "test-model"

    events = list(app.stream_chat("你好"))

    delta_events = [event for event in events if event["type"] == "delta"]

    assert [event["delta"] for event in delta_events] == ["你好", "，我是流式回复"]
    assert events[-1]["payload"]["final_answer"] == "你好，我是流式回复"
    assert "source=model" in str(events[-1]["payload"]["execution_trace"][-1]["detail"])
    assert events[-1]["payload"]["execution_trace"][0]["name"] == "router"
    assert any(call.get("stream") is True for call in app.context.application_context.llm_client.completions.calls)
