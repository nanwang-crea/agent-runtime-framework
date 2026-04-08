from types import SimpleNamespace

def test_create_workflow_node_executors_includes_current_node_families():
    from agent_runtime_framework.workflow.nodes import create_workflow_node_executors

    executors = create_workflow_node_executors()

    assert "aggregate_results" in executors
    assert "final_response" in executors
    assert "verification" in executors
    assert "interpret_target" in executors
    assert "plan_search" in executors
    assert "plan_read" in executors
    assert "create_path" in executors
    assert "apply_patch" in executors
    assert "content_search" in executors
    assert "chunked_file_read" in executors


def test_chat_service_uses_shared_workflow_node_registry(monkeypatch):
    from agent_runtime_framework.api.services.chat_service import ChatService

    captured = {}

    def _fake_registry():
        captured["called"] = True
        return {"noop": object()}

    monkeypatch.setattr("agent_runtime_framework.api.services.chat_service.create_workflow_node_executors", _fake_registry)

    runtime_state = SimpleNamespace(workflow_runtime_context=lambda: {})
    session_responses = SimpleNamespace()
    error_responses = SimpleNamespace()
    runtime = ChatService(runtime_state, session_responses, error_responses)._graph_runtime()

    assert captured["called"] is True
    assert runtime.executors == {"noop": runtime.executors["noop"]}
    assert runtime.context == {}
