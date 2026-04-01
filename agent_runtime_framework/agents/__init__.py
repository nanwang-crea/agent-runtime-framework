"""Agent implementations built on top of the framework kernel."""

from agent_runtime_framework.agents.builtin import builtin_agent_definitions
from agent_runtime_framework.agents.definitions import AgentDefinition
from agent_runtime_framework.agents.loader import extend_registry_from_dir, load_agent_definitions_from_dir
from agent_runtime_framework.agents.registry import AgentRegistry
from agent_runtime_framework.agents.workspace_backend import WorkspaceContext

__all__ = [
    "AgentDefinition",
    "AgentRegistry",
    "WorkspaceContext",
    "builtin_agent_definitions",
    "extend_registry_from_dir",
    "load_agent_definitions_from_dir",
]
