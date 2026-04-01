from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AgentDisplayProfile:
    agent_id: str
    label: str
    color: str
    metadata: dict[str, object] = field(default_factory=dict)
