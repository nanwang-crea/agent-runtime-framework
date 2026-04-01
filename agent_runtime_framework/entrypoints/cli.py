from __future__ import annotations

from agent_runtime_framework.entrypoints.models import AgentRequest
from agent_runtime_framework.entrypoints.sdk import run_agent_request


def run_cli_entry(app, *, message: str, agent_id: str = "workspace"):
    return run_agent_request(app, AgentRequest(message=message, agent_id=agent_id))
