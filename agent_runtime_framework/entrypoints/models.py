from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentRequest:
    message: str
    agent_id: str = "workspace"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResponse:
    status: str
    agent_id: str
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)
