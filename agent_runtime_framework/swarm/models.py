from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SwarmState:
    root_session_id: str
    active_session_ids: list[str] = field(default_factory=list)
    handoffs: list[dict[str, object]] = field(default_factory=list)
