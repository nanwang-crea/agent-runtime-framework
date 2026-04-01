from __future__ import annotations

from agent_runtime_framework.agents.workspace_backend.tools.file_tools import build_file_tools
from agent_runtime_framework.agents.workspace_backend.tools.shell_tools import build_shell_tools


def build_default_workspace_tools():
    return [tool.to_spec() for tool in [*build_file_tools(), *build_shell_tools()]]
