from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.models import AuthSession, ModelProfile
from agent_runtime_framework.demo import create_demo_assistant_app
from agent_runtime_framework.demo import app as demo_app_module
from agent_runtime_framework.demo.server import _load_asset
from agent_runtime_framework.workflow.models import GoalSpec, WorkflowEdge, WorkflowGraph, WorkflowNode
from agent_runtime_framework.workflow import conversation
from agent_runtime_framework.workflow.goal_intake import build_goal_envelope




class _ConversationLLM:
    def __init__(self, content: str = "我可以继续和你对话。") -> None:
        self.content = content
        self.completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=self.completions)
        self.calls: list[dict] = []

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=self.content))])])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def _install_conversation_model(app, content: str = "我可以继续和你对话。"):
    app.context.application_context.llm_client = _ConversationLLM(content)
    app.context.application_context.llm_model = "test-model"
    return app.context.application_context.llm_client

def _create_demo_assistant_app_with_test_planner(workspace: Path):
    return create_demo_assistant_app(workspace)


def _register_router_model(app, route: str):
    class _RouterClient:
        def create_chat_completion(self, _request):
            return SimpleNamespace(content=json.dumps({"route": route}))

    class _RouterInstance:
        instance_id = f"router_{route}"

        def list_models(self):
            return [
                ModelProfile(
                    instance=self.instance_id,
                    model_name=f"router-{route}-model",
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
    app.model_registry.authenticate(_RouterInstance.instance_id, {"api_key": "secret"})
    app.model_router.set_route("router", instance_id=_RouterInstance.instance_id, model_name=f"router-{route}-model")


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
    assert payload["capability_name"] == "workflow"
    assert payload["plan_history"][-1]["steps"][0]["capability_name"] == "goal_intake"


def test_demo_assistant_app_exposes_goal_envelope_and_runtime_context_helpers(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    goal = build_goal_envelope(
        "读取 README.md",
        application_context=app.context.application_context,
        workspace_root=workspace,
    )
    runtime_context = app._workflow_runtime_context()

    assert goal.goal == "读取 README.md"
    assert goal.intent == "file_read"
    assert goal.target_hints == ["README.md"]
    assert runtime_context["application_context"] is app.context.application_context
    assert runtime_context["workspace_context"] is app.context
    assert "memory" in runtime_context
    assert "policy_context" in runtime_context
    assert runtime_context["session_memory_snapshot"].focused_resources == []


def test_demo_assistant_app_can_replay_run_by_run_id(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)
    first = app.chat("读取 README.md")
    replayed = app.replay(first["run_id"])

    assert replayed["status"] == "completed"
    assert replayed["final_answer"] == "line one\nline two\nline three"


def test_demo_assistant_app_persists_markdown_memory_records(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)
    payload = app.chat("读取 README.md")

    assert payload["status"] == "completed"
    memory_file = workspace / ".arf" / "memory.md"
    assert memory_file.exists()
    persisted = memory_file.read_text(encoding="utf-8")
    assert "workspace_focus" in persisted
    assert "README.md" in persisted


def test_demo_assistant_app_resumes_clarification_loop_across_turns(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    (src / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (docs / "service.md").write_text("# service docs\n", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    first = app.chat("请讲解 service 这个模块在做什么")
    second = app.chat("src/service.py")

    assert first["status"] == "needs_clarification"
    assert "多个可能目标" in first["final_answer"]
    assert second["status"] == "completed"
    assert "src/service.py" in second["final_answer"]


def test_demo_assistant_app_resumes_clarification_after_restart(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    (src / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (docs / "service.md").write_text("# service docs\n", encoding="utf-8")
    first_app = _create_demo_assistant_app_with_test_planner(workspace)

    first = first_app.chat("请讲解 service 这个模块在做什么")

    restarted_app = _create_demo_assistant_app_with_test_planner(workspace)
    second = restarted_app.chat("src/service.py")

    assert first["status"] == "needs_clarification"
    assert second["status"] == "completed"
    assert "src/service.py" in second["final_answer"]


def test_demo_assistant_app_routes_normal_chat_without_conversation_module_import(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)
    _install_conversation_model(app)

    payload = app.chat("你是谁？")

    assert payload["status"] == "completed"
    assert payload["runtime"] == "workflow"
    assert payload["capability_name"] == "conversation"


def test_demo_assistant_app_exposes_route_by_goal_for_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)
    _install_conversation_model(app)

    payload = app.chat("你是谁？")

    trace_names = [step["name"] for step in payload["execution_trace"]]
    assert "goal_intake" in trace_names
    assert "route_by_goal" in trace_names


def test_demo_assistant_app_routes_normal_chat_to_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)
    _install_conversation_model(app)

    payload = app.chat("你是谁？")

    assert payload["status"] == "completed"
    assert payload["runtime"] == "workflow"
    assert payload["capability_name"] == "conversation"
    assert "我可以继续和你对话" in payload["final_answer"]
    assert payload["execution_trace"]
    assert payload["execution_trace"][-1]["name"] == "final_response"


def test_demo_app_reuses_shared_conversation_message_builder():
    assert demo_app_module.build_conversation_messages is conversation.build_conversation_messages


def test_workflow_package_exports_shared_conversation_message_builder():
    from agent_runtime_framework import workflow

    assert workflow.build_conversation_messages is conversation.build_conversation_messages


def test_demo_assets_are_loadable():
    html = _load_asset("index.html")

    assert "桌面端 AI 工具" in html
    assert 'src="/assets/' in html or 'href="/assets/' in html

    asset_paths = []
    for marker in ('src="/assets/', 'href="/assets/'):
        start = 0
        while True:
            index = html.find(marker, start)
            if index < 0:
                break
            begin = index + len('src="/' if marker.startswith('src') else 'href="/')
            end = html.find('"', begin)
            asset_paths.append(html[begin:end])
            start = end + 1

    loaded_assets = [_load_asset(path) for path in asset_paths]
    assert loaded_assets
    assert any(len(asset.strip()) > 20 for asset in loaded_assets)
    assert any(":root" in asset or "body" in asset or "background" in asset for asset in loaded_assets)


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


def test_demo_assistant_app_logs_unknown_errors_with_trace_id(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    def _boom(self, _message: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(type(app), "_run_workflow", _boom)

    with caplog.at_level("ERROR"):
        payload = app.chat("读取 README.md")

    assert payload["status"] == "error"
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["error"]["trace_id"]
    assert payload["error"]["context"]["workspace"] == str(workspace)
    assert payload["error"]["context"]["active_agent"] == "workspace"
    assert any(payload["error"]["trace_id"] in record.message for record in caplog.records)


def test_demo_assistant_app_model_center_action_raises_structured_error(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    with pytest.raises(AppError) as exc_info:
        app.run_model_center_action("unknown_action")

    assert exc_info.value.code == "MODEL_CENTER_ACTION_UNKNOWN"
    assert exc_info.value.stage == "model_center"
    assert exc_info.value.context["action"] == "unknown_action"


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


def test_demo_assistant_app_redacts_api_keys_from_model_center_payload(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    app.update_model_center(
        {
            "instances": {
                "openai": {
                    "credentials": {"api_key": "sk-secret-1234"},
                    "connection": {"base_url": "https://api.openai.com/v1"},
                }
            }
        }
    )

    payload = app.model_center_payload()
    openai_config = payload["config"]["instances"]["openai"]

    assert openai_config["credentials"] == {}
    assert openai_config["api_key_set"] is True
    assert openai_config["api_key_preview"] == "sk-s***1234"


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


def test_demo_assistant_app_keeps_existing_api_key_when_update_omits_new_key(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    app.update_model_center(
        {
            "instances": {
                "dashscope": {
                    "credentials": {"api_key": "sk-test"},
                    "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
                }
            }
        }
    )

    app.update_model_center(
        {
            "instances": {
                "dashscope": {
                    "credentials": {"api_key": ""},
                    "connection": {"base_url": "https://example.com/proxy/v1"},
                }
            }
        }
    )

    persisted = json.loads((workspace / ".arf_demo_config.json").read_text(encoding="utf-8"))

    assert persisted["instances"]["dashscope"]["credentials"]["api_key"] == "sk-test"
    assert persisted["instances"]["dashscope"]["connection"]["base_url"] == "https://example.com/proxy/v1"


def test_demo_assistant_app_streams_chat_events(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)
    _install_conversation_model(app)

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
    _install_conversation_model(app)

    events = list(app.stream_chat("你是谁？", chunk_size=8))
    payload = events[-1]["payload"]

    assert payload["context"]["active_agent"] in {"workspace", "qa_only"}
    assert payload["context"]["active_workspace"] == str(workspace)
    assert payload["context"]["available_agents"]


def test_demo_assistant_app_returns_error_for_conversation_without_model(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    payload = app.chat("你是谁？")

    assert payload["status"] == "error"
    assert "模型" in payload["final_answer"] or "llm" in payload["final_answer"].lower()



def test_demo_assistant_app_stream_returns_error_for_conversation_without_model(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    events = list(app.stream_chat("你是谁？"))

    assert events[-1]["type"] == "error"


def test_demo_assistant_app_stream_conversation_uses_unified_stream_path(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)
    _install_conversation_model(app, "你好")

    assert not hasattr(type(app), "_stream_workflow_conversation")
    assert not hasattr(demo_app_module, "stream_conversation_reply")

    events = list(app.stream_chat("你是谁？", chunk_size=8))

    assert events[-1]["type"] == "final"
    assert events[-1]["payload"]["status"] == "completed"
    assert events[-1]["payload"]["capability_name"] == "conversation"



def test_demo_assistant_app_emits_single_delta_for_fallback_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)
    _install_conversation_model(app, "你好")

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


def test_demo_assistant_app_recovers_from_directory_summarize_request(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs_dir = workspace / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text("# Guide\n", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    events = list(app.stream_chat("总结 docs"))

    error_events = [event for event in events if event["type"] == "error"]
    final_events = [event for event in events if event["type"] == "final"]

    assert not error_events
    assert final_events
    assert final_events[-1]["payload"]["status"] == "completed"
    assert "guide.md" in final_events[-1]["payload"]["final_answer"]


def test_demo_assistant_app_does_not_install_next_action_planner_by_default(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    app = create_demo_assistant_app(workspace)

    assert "next_action_planner" not in app.context.services



def test_demo_assistant_app_workspace_subtask_ignores_next_action_planner_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agent_runtime_framework.workflow.models import GoalSpec

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = create_demo_assistant_app(workspace)
    def _fake_analyze_goal(_message: str, context=None):
        return GoalSpec(original_goal="编辑 README.md 并验证修改结果", primary_intent="change_and_verify")

    def _unexpected_planner(*_args, **_kwargs):
        raise AssertionError("legacy next_action_planner should not be consulted")

    monkeypatch.setattr("agent_runtime_framework.demo.app.analyze_goal", _fake_analyze_goal)
    app.context.services["next_action_planner"] = _unexpected_planner

    payload = app.chat("编辑 README.md 并验证修改结果")

    assert payload["status"] == "completed"
    assert payload["runtime"] == "workflow"
    assert any(step["name"] == "workspace_subtask" for step in payload["execution_trace"])


def test_demo_assistant_app_requires_llm_for_codex_agent_planning(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    payload = app.chat("列一下当前工作区都有什么文件")

    assert payload["status"] == "completed"
    assert payload["runtime"] == "workflow"
    assert "README.md" in payload["final_answer"]
    assert any(step["detail"] in {"workspace_discovery", "workspace_subtask"} for step in payload["execution_trace"])


def test_demo_assistant_app_routes_plain_greeting_without_planner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)
    _install_conversation_model(app, "你好")
    payload = app.chat("你好")

    assert payload["status"] == "completed"
    assert payload["capability_name"] == "conversation"
    assert payload["execution_trace"][-1]["name"] == "final_response"


def test_demo_assistant_app_stream_routes_plain_greeting_without_planner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)
    _install_conversation_model(app, "你好")
    events = list(app.stream_chat("你好"))

    assert events[-1]["type"] == "final"
    assert events[-1]["payload"]["status"] == "completed"
    assert events[-1]["payload"]["runtime"] == "workflow"
    assert events[-1]["payload"]["capability_name"] == "conversation"


def test_demo_assistant_app_ignores_router_conversation_hint_for_workspace_request(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("demo", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    _register_router_model(app, "conversation")

    payload = app.chat("读取 README.md")

    assert payload["status"] == "completed"
    assert payload["runtime"] == "workflow"
    assert payload["capability_name"] == "workflow"
    assert any(step["detail"] == "content_search" for step in payload["execution_trace"])
    assert any(step["detail"] == "chunked_file_read" for step in payload["execution_trace"])


def test_demo_context_payload_includes_sandbox_state(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    from agent_runtime_framework.demo.app import create_demo_assistant_app

    app = create_demo_assistant_app(workspace)

    context_payload = app.context_payload()

    assert context_payload["sandbox"]["mode"] == "workspace_write"
    assert context_payload["sandbox"]["workspace_root"] == str(workspace.resolve())


def test_demo_context_payload_includes_active_persona(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    context_payload = app.context_payload()

    assert context_payload["active_persona"] == "general"


def test_demo_assistant_app_stream_returns_model_unavailable_without_final_payload(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    events = list(app.stream_chat("列一下当前工作区都有什么文件"))

    assert [event["type"] for event in events][-1] == "final"
    assert events[-1]["payload"]["status"] == "completed"
    assert "README.md" in events[-1]["payload"]["final_answer"]


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


def test_demo_assistant_app_uses_workspace_loop_for_workspace_actions(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)
    payload = app.chat("读取 README.md")

    assert payload["status"] == "completed"
    assert payload["runtime"] == "workflow"
    assert payload["execution_trace"][0]["name"] == "router"
    assert any(step["detail"] in {"content_search", "workspace_subtask"} for step in payload["execution_trace"])
    assert payload["execution_trace"][-1]["detail"] == "final_response"


def test_demo_assistant_app_explains_directory_structure_with_specialized_pattern(tmp_path: Path):
    workspace = tmp_path / "workspace"
    package = workspace / "agent_runtime_framework"
    assistant = package / "assistant"
    workspace.mkdir()
    package.mkdir()
    assistant.mkdir()
    (package / "__init__.py").write_text('"""Runtime package entry."""\n', encoding="utf-8")
    (assistant / "conversation.py").write_text(
        '"""Conversation routing helpers."""\n\n'
        "def route_user_message(text: str) -> str:\n"
        '    return "conversation"\n',
        encoding="utf-8",
    )
    app = create_demo_assistant_app(workspace)

    payload = app.chat("我想知道 agent_runtime_framework 目录下面主要都在讲些什么，各个子文件之间都有什么功能？")

    assert payload["status"] == "completed"
    assert payload["execution_trace"][0]["name"] == "router"
    assert payload["runtime"] == "workflow"
    assert any(step["detail"] == "workspace_discovery" for step in payload["execution_trace"])
    assert payload["execution_trace"][-1]["detail"] == "final_response"
    assert "agent_runtime_framework" in payload["final_answer"]
    assert "assistant/" in payload["final_answer"]
    assert "__init__.py" in payload["final_answer"]


def test_demo_assistant_app_compacts_large_trace_and_plan_details(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    large_text = "A" * 8000
    (workspace / "README.md").write_text(large_text, encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.chat("读取 README.md")

    assert payload["final_answer"].startswith("A" * 200)
    assert "已截断" in payload["final_answer"]
    assert len(str(payload["execution_trace"][0]["detail"])) < 400
    assert len(str(payload["plan_history"][-1]["steps"][0]["observation"])) < 400


def test_demo_assistant_app_can_switch_agent_profile_within_session(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = _create_demo_assistant_app_with_test_planner(workspace)

    payload = app.switch_context(agent_profile="qa_only")

    assert payload["context"]["active_agent"] == "qa_only"
    assert any(profile["id"] == "workspace" for profile in payload["context"]["available_agents"])
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


def test_demo_assistant_app_qa_only_profile_does_not_bypass_workflow_for_workspace_request(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("demo", encoding="utf-8")
    app = create_demo_assistant_app(workspace)
    app.switch_context(agent_profile="qa_only")

    payload = app.chat("读取 README.md")

    assert payload["runtime"] == "workflow"
    assert any(step["detail"] == "content_search" for step in payload["execution_trace"])


def test_demo_assistant_app_uses_llm_streaming_for_conversation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)
    app.switch_context(agent_profile="qa_only")
    app.context.application_context.llm_client = _StreamingLLM()
    app.context.application_context.llm_model = "test-model"

    events = list(app.stream_chat("你好"))

    delta_events = [event for event in events if event["type"] == "delta"]

    assert len(delta_events) == 1
    assert events[-1]["payload"]["final_answer"] == "你好，我是非流式回复"
    assert events[-1]["payload"]["execution_trace"][-1]["name"] == "final_response"


def test_demo_assistant_app_routes_non_compound_file_read_through_workflow(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)
    payload = app.chat("读取 README.md")

    assert payload["runtime"] == "workflow"
    assert payload["capability_name"] == "workflow"
    assert any(step["detail"] == "content_search" for step in payload["execution_trace"])



def test_demo_assistant_app_routes_clarification_followup_through_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    (src / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (docs / "service.md").write_text("# service docs\n", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)

    first = app.chat("请讲解 service 这个模块在做什么")

    workflow_calls: list[str] = []

    def _fake_run_workflow(self, message: str):
        workflow_calls.append(message)
        return {
            "status": "completed",
            "run_id": "workflow-follow-up",
            "plan_id": "workflow-follow-up",
            "final_answer": "src/service.py\nworkflow follow-up",
            "capability_name": "workflow",
            "runtime": "workflow",
            "execution_trace": [{"name": "workflow_followup", "status": "completed", "detail": "workflow"}],
            "approval_request": None,
            "resume_token_id": None,
            "session": app.session_payload(),
            "plan_history": app.plan_history_payload(),
            "run_history": app.run_history_payload(),
            "memory": app.memory_payload(),
            "context": app.context_payload(),
            "workspace": str(app.workspace),
        }

    monkeypatch.setattr(type(app), "_run_workflow", _fake_run_workflow)

    second = app.chat("src/service.py")

    assert first["status"] == "needs_clarification"
    assert workflow_calls == ["src/service.py"]
    assert second["runtime"] == "workflow"
    assert second["capability_name"] == "workflow"



def test_demo_assistant_app_routes_simple_file_read_without_workspace_subtask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = _create_demo_assistant_app_with_test_planner(workspace)
    def _unexpected_run_workspace_subtask(*_args, **_kwargs):
        raise AssertionError("simple workflow-native file reads should not call workspace subtask runner")

    monkeypatch.setattr(type(app), "_run_workspace_subtask", lambda self, *args, **kwargs: _unexpected_run_workspace_subtask(*args, **kwargs))

    payload = app.chat("读取 README.md")

    assert payload["runtime"] == "workflow"
    assert payload["capability_name"] == "workflow"
    assert any(step["detail"] == "content_search" for step in payload["execution_trace"])



def test_demo_assistant_app_runs_workspace_subtask_without_legacy_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agent_runtime_framework.workflow.models import GoalSpec

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    def _fake_analyze_goal(_message: str, context=None):
        return GoalSpec(original_goal="编辑 README.md 并验证修改结果", primary_intent="change_and_verify")

    def _fake_run_workspace_subtask(goal: str, *, task_profile: str, metadata: dict[str, object]):
        from agent_runtime_framework.agents.workspace_backend.models import WorkspaceTask, TaskState, VerificationResult
        from agent_runtime_framework.workflow.workspace_subtask import WorkspaceSubtaskResult

        task = WorkspaceTask(goal=goal, actions=[], task_profile=task_profile, state=TaskState())
        task.summary = "changed README"
        return WorkspaceSubtaskResult(status="completed", final_output="changed README", task=task, action_kind="workspace_subtask", run_id="subtask-run")

    monkeypatch.setattr("agent_runtime_framework.demo.app.analyze_goal", _fake_analyze_goal)
    monkeypatch.setattr(type(app), "_run_workspace_subtask", lambda self, *args, **kwargs: _fake_run_workspace_subtask(*args, **kwargs))

    payload = app.chat("编辑 README.md 并验证修改结果")

    assert payload["runtime"] == "workflow"
    assert any(step["name"] == "workspace_subtask" for step in payload["execution_trace"])


def test_demo_assistant_app_runs_unsupported_workspace_goal_inside_workflow_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agent_runtime_framework.workflow.models import GoalSpec
    from agent_runtime_framework.workflow.workspace_subtask import WorkspaceSubtaskResult
    from agent_runtime_framework.agents.workspace_backend.models import WorkspaceTask, TaskState, VerificationResult

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = create_demo_assistant_app(workspace)
    def _fake_analyze_goal(_message: str, context=None):
        return GoalSpec(original_goal="编辑 README.md 并验证修改结果", primary_intent="change_and_verify")

    def _fake_run_workspace_subtask(goal: str, *, task_profile: str, metadata: dict[str, object]):
        task = WorkspaceTask(goal=goal, actions=[], task_profile=task_profile, state=TaskState())
        task.summary = "changed README"
        return WorkspaceSubtaskResult(status="completed", final_output="changed README", task=task, action_kind="workspace_subtask", run_id="workflow-subtask-run")

    monkeypatch.setattr("agent_runtime_framework.demo.app.analyze_goal", _fake_analyze_goal)
    monkeypatch.setattr(type(app), "_run_workspace_subtask", lambda self, *args, **kwargs: _fake_run_workspace_subtask(*args, **kwargs))

    payload = app.chat("编辑 README.md 并验证修改结果")

    assert payload["runtime"] == "workflow"
    assert payload["capability_name"] == "workflow"
    assert any(step["name"] == "workspace_subtask" for step in payload["execution_trace"])



def test_demo_assistant_app_preserves_workflow_approval_resume_for_workspace_subtask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agent_runtime_framework.agents.workspace_backend.models import WorkspaceTask, TaskState, VerificationResult
    from agent_runtime_framework.assistant.approval import ApprovalRequest
    from agent_runtime_framework.workflow.models import GoalSpec
    from agent_runtime_framework.workflow.workspace_subtask import WorkspaceSubtaskResult

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    def _fake_analyze_goal(_message: str, context=None):
        return GoalSpec(
            original_goal="直接删除 README.md",
            primary_intent="dangerous_change",
            metadata={"requires_approval": True},
        )

    def _fake_run_workspace_subtask(goal: str, *, task_profile: str, metadata: dict[str, object]):
        task = WorkspaceTask(goal=goal, actions=[], task_profile=task_profile, state=TaskState())
        if metadata.get("approved"):
            task.summary = "dangerous change completed"
            task.verification = VerificationResult(success=True, summary="verified after approval")
            return WorkspaceSubtaskResult(status="completed", final_output="dangerous change completed", task=task, action_kind="workspace_subtask", run_id="approval-run")
        task.summary = "dangerous change pending approval"
        return WorkspaceSubtaskResult(
            status="waiting_approval",
            final_output="dangerous change pending approval",
            task=task,
            action_kind="workspace_subtask",
            approval_request=ApprovalRequest(
                capability_name="workspace_subtask",
                instruction="删除 README.md",
                reason="删除文件需要审批",
                risk_class="destructive",
            ),
            resume_token="workspace-subtask-token",
            run_id="approval-run",
        )

    monkeypatch.setattr("agent_runtime_framework.demo.app.analyze_goal", _fake_analyze_goal)
    monkeypatch.setattr(
        type(app),
        "_build_compat_workflow_runner",
        lambda self: type("_CompatRunner", (), {"compile_for_goal": lambda _self, goal: (_ for _ in ()).throw(AssertionError("approval path should not require compat graph compiler")), "run": lambda _self, message, graph, root_graph=None: (_ for _ in ()).throw(AssertionError("approval path should not require compat graph compiler"))})(),
    )
    monkeypatch.setattr(type(app), "_run_workspace_subtask", lambda self, *args, **kwargs: _fake_run_workspace_subtask(*args, **kwargs))

    first = app.chat("直接删除 README.md")

    assert first["status"] == "waiting_approval"
    assert first["runtime"] == "workflow"
    assert first["resume_token_id"]
    assert first["approval_request"]["risk_class"] == "destructive"

    resumed = app.approve(first["resume_token_id"], approved=True)

    assert resumed["status"] == "completed"
    assert resumed["runtime"] == "workflow"
    assert resumed["final_answer"] == "dangerous change completed"



def test_demo_assistant_app_routes_module_question_through_evidence_chain(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    (src / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    payload = app.chat("请讲解 service 这个模块在做什么")

    assert payload["runtime"] == "workflow"
    assert any(step["name"] == "target_resolution" for step in payload["execution_trace"])
    assert any(step["name"] == "content_search" for step in payload["execution_trace"])
    assert any(step["name"] == "chunked_file_read" for step in payload["execution_trace"])
    assert any(step["name"] == "evidence_synthesis" for step in payload["execution_trace"])


def test_demo_assistant_app_can_run_evidence_synthesis_node_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    monkeypatch.setattr(
        demo_app_module,
        "analyze_goal",
        lambda _message, context=None: GoalSpec(
            original_goal="列一下当前工作区都有什么文件",
            primary_intent="repository_overview",
            requires_repository_overview=True,
        ),
    )
    payload = app.chat("列一下当前工作区都有什么文件")

    assert payload["status"] == "completed"
    assert any(step["detail"] == "evidence_synthesis" for step in payload["execution_trace"])
    assert payload["execution_trace"][-1]["detail"] == "final_response"


def test_demo_assistant_app_includes_structured_evidence_payload(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    (src / "service.py").write_text("class BillingService:\n    pass\n", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    payload = app.chat("读取 src/service.py")

    assert payload["status"] == "completed"
    assert payload["evidence"]["candidates"]
    assert payload["evidence"]["chunks"]
    assert payload["evidence"]["verification"]["status"] in {"passed", "not_run"}
    assert any(item["path"].endswith("service.py") for item in payload["evidence"]["candidates"])


def test_demo_workflow_runtime_registers_only_new_native_executors(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    runtime = app._build_graph_execution_runtime()

    assert {"workspace_discovery", "content_search", "chunked_file_read", "evidence_synthesis"} <= set(runtime.executors)
    assert "repository_explainer" not in runtime.executors
    assert "file_reader" not in runtime.executors


def test_demo_assistant_app_rejects_legacy_file_reader_graph(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("legacy reader content", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    payload = app._build_compat_workflow_runner().run(
        "读取 README.md",
        graph=WorkflowGraph(
            nodes=[
                WorkflowNode(node_id="file_read", node_type="file_reader", metadata={"target_path": "README.md"}),
                WorkflowNode(node_id="final_response", node_type="final_response", dependencies=["file_read"]),
            ],
            edges=[WorkflowEdge(source="file_read", target="final_response")],
        ),
    )

    assert payload["status"] == "failed"
    assert any(step["detail"] == "file_reader" for step in payload["execution_trace"])
    assert payload["final_answer"] == ""



def test_demo_assistant_stream_chat_does_not_delegate_to_chat_for_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    analyze_calls = {"count": 0}

    def _fake_analyze_goal(_message: str, context=None):
        analyze_calls["count"] += 1
        return GoalSpec(original_goal="列一下当前工作区", primary_intent="repository_overview", requires_repository_overview=True)

    monkeypatch.setattr(demo_app_module, "analyze_goal", _fake_analyze_goal)
    monkeypatch.setattr(type(app), "chat", lambda self, message: (_ for _ in ()).throw(AssertionError("stream_chat should not call chat")))
    monkeypatch.setattr(
        type(app),
        "_run_agent_graph_workflow",
        lambda self, message, **kwargs: {
            "status": "completed",
            "run_id": "run-1",
            "plan_id": "run-1",
            "final_answer": "done",
            "capability_name": "workflow",
            "runtime": "workflow",
            "execution_trace": [{"name": "judge_1", "status": "completed", "detail": "judge"}],
            "approval_request": None,
            "resume_token_id": None,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": str(self.workspace),
            "judge": {"status": "accepted", "reason": "ok"},
            "planned_subgraphs": [{"iteration": 1}],
            "graph_state_summary": {"current_iteration": 1, "appended_node_ids": []},
            "append_history": [],
        },
    )

    events = list(app.stream_chat("列一下当前工作区"))

    assert events[-1]["type"] == "final"
    assert events[-1]["payload"]["status"] == "completed"
    assert analyze_calls["count"] == 1


def test_demo_assistant_app_routes_chat_through_root_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    monkeypatch.setattr(
        type(app),
        "_build_root_graph_runtime",
        lambda self: type("_FakeRootRuntime", (), {
            "run": lambda _self, message, stream=False: {
                "status": "completed",
                "run_id": "root-run",
                "plan_id": "root-run",
                "final_answer": "root payload",
                "capability_name": "workflow",
                "runtime": "workflow",
                "execution_trace": [{"name": "goal_intake", "status": "completed", "detail": "goal_intake"}],
                "approval_request": None,
                "resume_token_id": None,
                "session": app.session_payload(),
                "plan_history": app.plan_history_payload(),
                "run_history": app.run_history_payload(),
                "memory": app.memory_payload(),
                "context": app.context_payload(),
                "workspace": str(app.workspace),
            }
        })(),
    )

    payload = app.chat("读取 README.md")

    assert payload["status"] == "completed"
    assert payload["final_answer"] == "root payload"


def test_root_graph_runtime_passes_injected_context_to_goal_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)
    runtime = app._build_runtime_factory().build_root_graph_runtime()
    captured: dict[str, object] = {}
    injected_context = {"application_context": app.context.application_context, "services": {"source": "runtime"}}
    runtime.context = injected_context
    runtime.run_conversation = lambda message, graph, root_graph: {"status": "completed", "execution_trace": [], "final_answer": "ok"}
    runtime.run_agent = lambda message, goal, root_graph: {"status": "completed", "execution_trace": [], "final_answer": "ok"}

    def _capture(message: str, context=None):
        captured["context"] = context
        return GoalSpec(original_goal=message, primary_intent="generic")

    monkeypatch.setattr(demo_app_module, "analyze_goal", _capture)

    runtime.run("你是谁？")

    assert captured["context"] is injected_context


def test_demo_runtime_factory_analyze_goal_fn_uses_runtime_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)
    runtime = app._build_runtime_factory().build_root_graph_runtime()
    captured: dict[str, object] = {}
    injected_context = {"application_context": app.context.application_context, "services": {"source": "runtime"}}

    def _capture(message: str, context=None):
        captured["context"] = context
        return GoalSpec(original_goal=message, primary_intent="generic")

    monkeypatch.setattr(demo_app_module, "analyze_goal", _capture)

    runtime.analyze_goal_fn("你是谁？", injected_context)

    assert captured["context"] is injected_context


def test_runtime_factory_builds_root_runtime_with_named_services(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)
    runtime = app._build_runtime_factory().build_root_graph_runtime()

    assert runtime.analyze_goal_fn.__name__ == "_analyze_goal"
    assert runtime.mark_route_decision.__name__ == "_mark_route_decision"
    assert runtime.has_pending_clarification.__name__ == "_has_pending_clarification"
    assert runtime.run_conversation.__name__ == "_run_conversation_branch"
    assert runtime.run_agent.__name__ == "_run_agent_branch"


def test_root_graph_runtime_exports_typed_payload_contracts():
    from agent_runtime_framework.workflow.root_graph_runtime import RootGraphPayload, RuntimePayload

    assert set(RootGraphPayload.__annotations__) == {"route", "intent"}
    assert "execution_trace" in RuntimePayload.__annotations__
    assert "root_graph" in RuntimePayload.__annotations__



def test_demo_assistant_app_routes_stream_chat_through_root_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    monkeypatch.setattr(
        type(app),
        "_build_root_graph_runtime",
        lambda self: type("_FakeRootRuntime", (), {
            "run": lambda _self, message, stream=False: {
                "status": "completed",
                "run_id": "root-run",
                "plan_id": "root-run",
                "final_answer": "root payload",
                "capability_name": "workflow",
                "runtime": "workflow",
                "execution_trace": [{"name": "goal_intake", "status": "completed", "detail": "goal_intake"}],
                "approval_request": None,
                "resume_token_id": None,
                "session": app.session_payload(),
                "plan_history": app.plan_history_payload(),
                "run_history": app.run_history_payload(),
                "memory": app.memory_payload(),
                "context": app.context_payload(),
                "workspace": str(app.workspace),
            }
        })(),
    )

    events = list(app.stream_chat("读取 README.md"))

    assert events[-1]["type"] == "final"
    assert events[-1]["payload"]["final_answer"] == "root payload"


def test_demo_assistant_chat_non_conversation_does_not_require_build_workflow_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("line one\nline two\nline three", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    monkeypatch.setattr(
        demo_app_module,
        "analyze_goal",
        lambda _message, context=None: GoalSpec(
            original_goal="读取 README.md",
            primary_intent="file_read",
            requires_file_read=True,
            target_paths=["README.md"],
        ),
    )
