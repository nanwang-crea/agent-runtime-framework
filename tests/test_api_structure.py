from __future__ import annotations

import inspect
from typing import get_type_hints

from fastapi import FastAPI


def test_api_app_module_exposes_fastapi_factory():
    from agent_runtime_framework.api.app import create_app

    app = create_app()

    assert isinstance(app, FastAPI)


def test_api_app_factory_only_accepts_workspace_entrypoint():
    from agent_runtime_framework.api.app import create_app

    signature = inspect.signature(create_app)

    assert list(signature.parameters) == ["workspace"]


def test_api_server_module_exposes_cli_entrypoint():
    from agent_runtime_framework.api import server

    assert callable(server.main)


def test_demo_assistant_app_is_not_backend_main_entrypoint():
    from agent_runtime_framework.api import app, server

    app_source = inspect.getsource(app)
    server_source = inspect.getsource(server)

    assert "DemoAssistantApp" not in app_source
    assert "DemoAssistantApp" not in server_source


def test_api_service_modules_expose_route_facing_services():
    from agent_runtime_framework.api.services.chat_service import ChatService
    from agent_runtime_framework.api.services.context_service import ContextService
    from agent_runtime_framework.api.services.run_service import RunService
    from agent_runtime_framework.api.services.session_service import SessionService

    assert ChatService is not None
    assert ContextService is not None
    assert RunService is not None
    assert SessionService is not None


def test_api_route_modules_exist_for_split_http_surface():
    from agent_runtime_framework.api.routes import chat_routes, context_routes, model_center_routes, run_routes, session_routes

    assert chat_routes.router is not None
    assert context_routes.router is not None
    assert model_center_routes.router is not None
    assert run_routes.router is not None
    assert session_routes.router is not None


def test_api_app_factory_builds_routes_without_demo_route_module():
    from agent_runtime_framework.api import app

    app_source = inspect.getsource(app)

    assert "create_demo_api" not in app_source


def test_backend_entrypoints_do_not_depend_on_demo_runtime_shell():
    from agent_runtime_framework.api import app, server

    combined_source = inspect.getsource(app) + "\n" + inspect.getsource(server)

    assert "create_demo_assistant_app" not in combined_source


def test_api_package_no_longer_imports_demo_backend_modules():
    from pathlib import Path

    api_root = Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in api_root.rglob("*.py"))

    assert "agent_runtime_framework.demo" not in sources


def test_api_package_no_longer_uses_runtime_factory_module():
    from pathlib import Path

    api_root = Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api"

    assert not (api_root / "runtime_factory.py").exists()


def test_api_package_uses_classified_subdirectories_for_models_state_and_responses():
    from pathlib import Path

    api_root = Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api"

    assert (api_root / "state").is_dir()
    assert (api_root / "responses").is_dir()
    assert (api_root / "dependencies.py").exists() is False
    assert (api_root / "state" / "session_state.py").exists()
    assert (api_root / "responses" / "session_responses.py").exists()
    assert (api_root / "responses" / "error_responses.py").exists()
    assert (api_root / "responses" / "run_responses.py").exists()
    assert not (api_root / "responses" / "api_response_builder.py").exists()


def test_api_package_no_longer_keeps_workflow_control_layer_files():
    from pathlib import Path

    api_root = Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api"

    assert not (api_root / "services" / "workflow_service.py").exists()
    assert not (api_root / "run_lifecycle.py").exists()
    assert not (api_root / "services" / "run_lifecycle_service.py").exists()
    assert not (api_root / "agent_branch_orchestrator.py").exists()
    assert not (api_root / "workflow_branch_orchestrator.py").exists()
    assert not (api_root / "workflow_payload_builder.py").exists()
    assert not (api_root / "workflow_run_observer.py").exists()
    assert not (api_root / "pending_run_registry.py").exists()


def test_chat_and_run_services_do_not_depend_on_workflow_service_shell():
    from agent_runtime_framework.api.services import chat_service, run_service

    chat_source = inspect.getsource(chat_service)
    run_source = inspect.getsource(run_service)

    assert "workflow_service" not in chat_source
    assert "workflow_service" not in run_source
    assert "workflow:" not in chat_source
    assert "workflow:" not in run_source


def test_runtime_state_no_longer_exposes_chat_behaviors():
    from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState

    assert not hasattr(ApiRuntimeState, "chat")
    assert not hasattr(ApiRuntimeState, "stream_chat")


def test_runtime_state_is_now_state_container_only():
    from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState

    assert not hasattr(ApiRuntimeState, "session_payload")
    assert not hasattr(ApiRuntimeState, "memory_payload")
    assert not hasattr(ApiRuntimeState, "plan_history_payload")
    assert not hasattr(ApiRuntimeState, "run_history_payload")
    assert not hasattr(ApiRuntimeState, "context_payload")
    assert not hasattr(ApiRuntimeState, "switch_context")
    assert not hasattr(ApiRuntimeState, "error_payload")
    assert not hasattr(ApiRuntimeState, "result_payload")


def test_api_services_depend_on_runtime_state_type_instead_of_any():
    from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState
    from agent_runtime_framework.api.services.chat_service import ChatService
    from agent_runtime_framework.api.services.context_service import ContextService
    from agent_runtime_framework.api.services.run_service import RunService
    from agent_runtime_framework.api.services.session_service import SessionService

    assert get_type_hints(ChatService)["runtime_state"] is ApiRuntimeState
    assert get_type_hints(ContextService)["runtime_state"] is ApiRuntimeState
    assert get_type_hints(RunService)["runtime_state"] is ApiRuntimeState
    assert get_type_hints(SessionService)["runtime_state"] is ApiRuntimeState


def test_api_package_no_longer_keeps_demo_naming():
    from pathlib import Path

    api_root = Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in api_root.rglob("*.py"))

    assert "DemoSessionState" not in sources
    assert "builtin_demo_profiles" not in sources
    assert "get_demo_profile" not in sources
    assert "normalize_demo_error" not in sources
    assert ".arf_demo_config.json" not in sources
