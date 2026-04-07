from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_runtime_framework.api.models.profiles import get_profile
from agent_runtime_framework.api.presenters.response_builder import ApiResponseBuilder
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.sandbox import SandboxConfig


@dataclass(slots=True)
class ContextService:
    runtime_state: ApiRuntimeState
    response_builder: ApiResponseBuilder

    def switch_context(self, *, agent_profile: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        if agent_profile:
            if get_profile(agent_profile) is None:
                raise ValueError(f"unknown agent profile: {agent_profile}")
            self.runtime_state._active_agent = agent_profile
        if workspace:
            next_workspace = Path(workspace).expanduser().resolve()
            if not next_workspace.exists():
                raise FileNotFoundError(next_workspace)
            self.runtime_state.workspace = next_workspace
            self.runtime_state.context.application_context.resource_repository = LocalFileResourceRepository([next_workspace])
            self.runtime_state.context.application_context.config["default_directory"] = str(next_workspace)
            sandbox = self.runtime_state.context.application_context.services.get("sandbox")
            if isinstance(sandbox, SandboxConfig):
                sandbox.workspace_root = next_workspace
                sandbox.writable_roots = [next_workspace]
            self.runtime_state._available_workspaces = list(dict.fromkeys([str(next_workspace), *self.runtime_state._available_workspaces]))
        return self.response_builder.session_snapshot()
