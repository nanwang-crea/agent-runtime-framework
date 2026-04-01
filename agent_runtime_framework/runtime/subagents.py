from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SubagentLink:
    parent_session_id: str
    child_session_id: str
    agent_id: str
    metadata: dict[str, object] = field(default_factory=dict)
