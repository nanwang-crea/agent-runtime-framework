from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime_framework.api.responses.session_responses import SessionResponseFactory
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.sandbox import SandboxConfig


@dataclass(slots=True)
class ContextService:
    runtime_state: ApiRuntimeState
    session_responses: SessionResponseFactory

    def switch_context(self, *, workspace: str | None = None) -> dict[str, Any]:
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
        return self.session_responses.session_snapshot()
