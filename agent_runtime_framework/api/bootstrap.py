from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.api.responses.error_responses import ErrorResponseFactory
from agent_runtime_framework.api.responses.session_responses import SessionResponseFactory
from agent_runtime_framework.api.services import ApiServices
from agent_runtime_framework.api.services.chat_service import ChatService
from agent_runtime_framework.api.services.context_service import ContextService
from agent_runtime_framework.api.services.model_center_service import ModelCenterService, ModelCenterStore
from agent_runtime_framework.api.services.run_service import RunService
from agent_runtime_framework.api.services.session_service import SessionService
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState
from agent_runtime_framework.api.state.session_state import SessionState
from agent_runtime_framework.memory import InMemorySessionMemory
from agent_runtime_framework.models import (
    CodexCliDriver,
    InMemoryCredentialStore,
    ModelRegistry,
    ModelRouter,
    OpenAICompatibleDriver,
)
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.sandbox import SandboxConfig
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.workflow.context.app_context import ApplicationContext
from agent_runtime_framework.workflow.state.persistence import WorkflowPersistenceStore
from agent_runtime_framework.workflow.workspace import WorkspaceContext, build_default_workspace_tools


def create_api_runtime_state(workspace: str | Path, *, seed_config: dict[str, Any] | None = None) -> ApiRuntimeState:
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace_path}")

    model_center_store = ModelCenterStore(workspace_path / ".arf_config.json")
    model_registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    model_router = ModelRouter(model_registry)
    model_center = ModelCenterService(
        store=model_center_store,
        registry=model_registry,
        router=model_router,
    )
    model_registry.register_driver(OpenAICompatibleDriver())
    model_registry.register_driver(CodexCliDriver())
    model_center.store.load_or_create(seed=seed_config)
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace_path]),
        session_memory=InMemorySessionMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace_path)},
        services={
            "model_registry": model_registry,
            "model_router": model_router,
            "sandbox": SandboxConfig(
                mode="workspace_write",
                workspace_root=workspace_path,
                writable_roots=[workspace_path],
                allow_network=False,
            ),
        },
    )
    for tool in build_default_workspace_tools():
        app_context.tools.register(tool)
    context = WorkspaceContext(
        application_context=app_context,
        services={},
        session=SessionState(session_id=str(uuid4())),
    )
    runtime_state = ApiRuntimeState(
        workspace=workspace_path,
        context=context,
        model_registry=model_registry,
        model_router=model_router,
        model_center=model_center,
        _pending_tokens={},
        _run_history=[],
        _task_history=[],
        _run_inputs={},
        _last_route_decision=None,
        _pending_workflow_interaction=None,
        _pending_workflow_clarification=None,
        _available_workspaces=[str(workspace_path)],
        _workflow_store=WorkflowPersistenceStore(workspace_path / ".arf" / "workflow-runs.json"),
    )
    runtime_state.model_center.load()
    return runtime_state


def create_api_services(workspace: str | Path, *, seed_config: dict[str, Any] | None = None) -> ApiServices:
    runtime_state = create_api_runtime_state(workspace, seed_config=seed_config)
    session_responses = SessionResponseFactory(runtime_state)
    error_responses = ErrorResponseFactory(runtime_state, session_responses)
    chat_service = ChatService(runtime_state, session_responses, error_responses)
    return ApiServices(
        session=SessionService(runtime_state, session_responses),
        chat=chat_service,
        context=ContextService(runtime_state, session_responses),
        runs=RunService(runtime_state, session_responses, chat_service=chat_service),
        model_center=runtime_state.model_center,
    )
