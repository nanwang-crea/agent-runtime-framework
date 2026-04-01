from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentToolCall:
    agent_id: str
    message: str
    requested_skills: list[str] = field(default_factory=list)
    enabled_skills: list[str] = field(default_factory=list)
    external_capability_hints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentToolResult:
    status: str
    agent_id: str
    output: str
    execution_backend: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentToolSpec:
    name: str
    default_agent_id: str
    description: str = ""
