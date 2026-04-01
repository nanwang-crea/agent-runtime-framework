from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(slots=True)
class AgentSessionRecord:
    agent_id: str
    goal: str
    session_id: str = field(default_factory=lambda: str(uuid4()))
    parent_session_id: str = ""
    run_id: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
