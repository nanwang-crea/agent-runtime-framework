from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SkillAttachment:
    skill_id: str
    required: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {"skill_id": self.skill_id, "required": self.required, "metadata": dict(self.metadata)}
