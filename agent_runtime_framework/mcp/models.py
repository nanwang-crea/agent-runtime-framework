from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class McpServiceRef:
    server_id: str
    label: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {"server_id": self.server_id, "label": self.label, "metadata": dict(self.metadata)}


@dataclass(frozen=True, slots=True)
class McpCapabilityRef:
    server_id: str
    capability_id: str
    metadata: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {"server_id": self.server_id, "capability_id": self.capability_id, "metadata": dict(self.metadata)}
