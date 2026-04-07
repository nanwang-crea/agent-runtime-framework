from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.demo.app import DemoAssistantApp
from agent_runtime_framework.demo.model_center import ModelCenterService, ModelCenterStore
from agent_runtime_framework.demo.profiles import builtin_demo_profiles
from agent_runtime_framework.demo.session_state import DemoSessionState
from agent_runtime_framework.errors import AppError
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
from agent_runtime_framework.workflow.application_context import ApplicationContext
from agent_runtime_framework.workflow.persistence import WorkflowPersistenceStore
from agent_runtime_framework.workflow.workspace import WorkspaceContext, build_default_workspace_tools


def create_demo_assistant_app(workspace: str | Path, *, seed_config: dict[str, Any] | None = None) -> DemoAssistantApp:
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace_path}")
    model_center_store = ModelCenterStore(workspace_path / ".arf_demo_config.json")
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
        session=DemoSessionState(session_id=str(uuid4())),
    )
    app = DemoAssistantApp(
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
        _pending_workflow_clarification=None,
        _active_agent="workspace",
        _available_workspaces=[str(workspace_path)],
        available_profiles=builtin_demo_profiles(),
        _workflow_store=WorkflowPersistenceStore(workspace_path / ".arf" / "workflow-runs.json"),
    )
    app.model_center.load()
    return app
