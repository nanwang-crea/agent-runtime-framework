from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MemoryItem:
    memory_id: str
    layer: str
    record_kind: str
    scope: str
    text: str
    path: str = ""
    entity_name: str = ""
    entity_type: str = "unknown"
    confidence: float = 0.0
    source_tool: str = ""
    source_task_profile: str = ""
    created_at: str = ""
    last_verified_at: str = ""
    retrievable_for_resolution: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        payload = dict(self.metadata)
        payload.update(
            {
                "layer": self.layer,
                "record_kind": self.record_kind,
                "scope": self.scope,
                "path": self.path,
                "entity_name": self.entity_name,
                "entity_type": self.entity_type,
                "confidence": self.confidence,
                "source_tool": self.source_tool,
                "source_task_profile": self.source_task_profile,
                "created_at": self.created_at,
                "last_verified_at": self.last_verified_at,
                "retrievable_for_resolution": self.retrievable_for_resolution,
            }
        )
        filtered: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, str) and not value:
                continue
            if isinstance(value, (list, dict)) and not value:
                continue
            filtered[key] = value
        return filtered
