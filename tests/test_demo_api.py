from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


def _stub_app():
    return SimpleNamespace(
        workspace=".",
        session_payload=lambda: {"session_id": "s", "turns": []},
        plan_history_payload=lambda: [],
        run_history_payload=lambda: [],
        memory_payload=lambda: {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None},
        context_payload=lambda: {"active_agent": "workspace", "available_agents": [], "active_workspace": ".", "available_workspaces": ["."], "sandbox": {}},
        model_center=SimpleNamespace(payload=lambda: {"config": {}, "runtime": {}, "runtime_checks": {}}, update=lambda payload: {"updated": payload}, run_action=lambda action, payload: {"action": action, "payload": payload}),
        chat=lambda message: {"status": "completed", "final_answer": message, "capability_name": "workflow", "execution_trace": [], "approval_request": None, "resume_token_id": None, "session": {"session_id": "s", "turns": []}, "plan_history": [], "run_history": [], "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None}, "context": {"active_agent": "workspace", "available_agents": [], "active_workspace": ".", "available_workspaces": ["."], "sandbox": {}}, "workspace": "."},
        stream_chat=lambda message: iter([
            {"type": "start", "message": message},
            {"type": "final", "payload": {"status": "completed", "final_answer": message, "capability_name": "workflow", "execution_trace": [], "approval_request": None, "resume_token_id": None, "session": {"session_id": "s", "turns": []}, "plan_history": [], "run_history": [], "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None}, "context": {"active_agent": "workspace", "available_agents": [], "active_workspace": ".", "available_workspaces": ["."], "sandbox": {}}, "workspace": "."}},
        ]),
        switch_context=lambda **kwargs: {"workspace": ".", "session": {"session_id": "s", "turns": []}, "plan_history": [], "run_history": [], "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None}, "context": {"active_agent": kwargs.get("agent_profile") or "workspace", "available_agents": [], "active_workspace": ".", "available_workspaces": ["."], "sandbox": {}}},
    )


def test_create_demo_api_exposes_core_routes():
    from agent_runtime_framework.demo.api import create_demo_api

    client = TestClient(create_demo_api(_stub_app()))

    session_response = client.get("/api/session")
    assert session_response.status_code == 200
    assert session_response.json()["workspace"] == "."

    chat_response = client.post("/api/chat", json={"message": "hello"})
    assert chat_response.status_code == 200
    assert chat_response.json()["final_answer"] == "hello"

    stream_response = client.post("/api/chat/stream", json={"message": "hello"})
    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")


def test_create_demo_api_validates_required_payload_fields():
    from agent_runtime_framework.demo.api import create_demo_api

    client = TestClient(create_demo_api(_stub_app()))

    response = client.post("/api/chat", json={})

    assert response.status_code == 400
    assert response.json()["error"] == "message is required"
