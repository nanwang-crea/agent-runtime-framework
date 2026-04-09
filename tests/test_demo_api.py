from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx


def _stub_app():
    return SimpleNamespace(
        workspace=".",
        session_payload=lambda: {"session_id": "s", "turns": []},
        plan_history_payload=lambda: [],
        memory_payload=lambda: {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None},
        context_payload=lambda: {"active_workspace": ".", "available_workspaces": ["."]},
        model_center=SimpleNamespace(payload=lambda: {"config": {}, "runtime": {}, "runtime_checks": {}}, update=lambda payload: {"updated": payload}, run_action=lambda action, payload: {"action": action, "payload": payload}),
        chat=lambda message: {"status": "completed", "final_answer": message, "execution_trace": [], "approval_request": None, "resume_token_id": None, "session": {"session_id": "s", "turns": []}, "plan_history": [], "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None}, "context": {"active_workspace": ".", "available_workspaces": ["."]}, "workspace": "."},
        stream_chat=lambda message: iter([
            {"type": "start", "message": message},
            {"type": "final", "payload": {"status": "completed", "final_answer": message, "execution_trace": [], "approval_request": None, "resume_token_id": None, "session": {"session_id": "s", "turns": []}, "plan_history": [], "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None}, "context": {"active_workspace": ".", "available_workspaces": ["."]}, "workspace": "."}},
        ]),
        switch_context=lambda **kwargs: {"workspace": kwargs.get("workspace") or ".", "session": {"session_id": "s", "turns": []}, "plan_history": [], "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None}, "context": {"active_workspace": kwargs.get("workspace") or ".", "available_workspaces": ["."]}},
    )


def _stub_services():
    return SimpleNamespace(
        session=SimpleNamespace(
            session_snapshot=lambda: {
                "workspace": ".",
                "session": {"session_id": "s", "turns": []},
                "plan_history": [],
                "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None},
                "context": {"active_workspace": ".", "available_workspaces": ["."]},
            }
        ),
        chat=SimpleNamespace(
            chat=lambda message: {"status": "completed", "final_answer": message},
            stream_chat=lambda message: iter([
                {"type": "start", "message": message},
                {"type": "final", "payload": {"status": "completed", "final_answer": message}},
            ]),
        ),
        context=SimpleNamespace(
            switch_context=lambda **kwargs: {
                "workspace": kwargs.get("workspace") or ".",
                "session": {"session_id": "s", "turns": []},
                "plan_history": [],
                "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None},
                "context": {"active_workspace": kwargs.get("workspace") or ".", "available_workspaces": ["."]},
            }
        ),
        runs=SimpleNamespace(
            approve=lambda token_id, approved: {"token_id": token_id, "approved": approved},
            replay=lambda run_id: {"run_id": run_id, "status": "completed"},
        ),
        model_center=SimpleNamespace(
            payload=lambda: {"config": {}, "runtime": {}, "runtime_checks": {}},
            update=lambda payload: {"updated": payload},
            run_action=lambda action, payload: {"action": action, "payload": payload},
        ),
    )


def test_create_demo_api_exposes_core_routes():
    from agent_runtime_framework.api.app import create_app
    from agent_runtime_framework.api.services import ApiServices

    async def _run():
        app = create_app()
        services = _stub_services()
        captured_context_kwargs = {}

        def _switch_context(**kwargs):
            captured_context_kwargs.clear()
            captured_context_kwargs.update(kwargs)
            return {
                "workspace": kwargs.get("workspace") or ".",
                "session": {"session_id": "s", "turns": []},
                "plan_history": [],
                "memory": {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None},
                "context": {"active_workspace": kwargs.get("workspace") or ".", "available_workspaces": ["."]},
            }

        services.context = SimpleNamespace(switch_context=_switch_context)
        app.state.api_services = ApiServices(**services.__dict__)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            session_response = await client.get("/api/session")
            assert session_response.status_code == 200
            assert session_response.json()["workspace"] == "."
            assert "active_agent" not in session_response.json()["context"]
            assert "available_agents" not in session_response.json()["context"]

            chat_response = await client.post("/api/chat", json={"message": "hello"})
            assert chat_response.status_code == 200
            assert chat_response.json()["final_answer"] == "hello"

            stream_response = await client.post("/api/chat/stream", json={"message": "hello"})
            assert stream_response.status_code == 200
            assert stream_response.headers["content-type"].startswith("text/event-stream")

            context_response = await client.post("/api/context", json={"workspace": "/tmp/demo-workspace"})
            assert context_response.status_code == 200
            assert context_response.json()["context"]["active_workspace"] == "/tmp/demo-workspace"
            assert captured_context_kwargs == {"workspace": "/tmp/demo-workspace"}

            approve_response = await client.post("/api/approve", json={"token_id": "t-1", "approved": True})
            assert approve_response.status_code == 200
            assert approve_response.json() == {"token_id": "t-1", "approved": True}

            replay_response = await client.post("/api/replay", json={"run_id": "r-1"})
            assert replay_response.status_code == 200
            assert replay_response.json()["run_id"] == "r-1"

    asyncio.run(_run())


def test_create_demo_api_validates_required_payload_fields():
    from agent_runtime_framework.api.app import create_app
    from agent_runtime_framework.api.services import ApiServices

    async def _run():
        app = create_app()
        app.state.api_services = ApiServices(**_stub_services().__dict__)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/chat", json={})

            assert response.status_code == 400
            assert response.json()["error"] == "message is required"

    asyncio.run(_run())
