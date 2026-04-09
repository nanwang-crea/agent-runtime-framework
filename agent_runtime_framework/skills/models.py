from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SkillAttachment:
    skill_id: str
    required: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {"skill_id": self.skill_id, "required": self.required, "metadata": dict(self.metadata)}


@dataclass(frozen=True, slots=True)
class SkillResult:
    name: str
    success: bool
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    changed_paths: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    memory_hint: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "summary": self.summary,
            "payload": dict(self.payload),
            "changed_paths": list(self.changed_paths),
            "references": list(self.references),
            "memory_hint": dict(self.memory_hint) if isinstance(self.memory_hint, dict) else None,
        }
