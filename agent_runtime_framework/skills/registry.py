from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime_framework.skills.models import SkillAttachment


@dataclass(slots=True)
class SkillRegistry:
    _skills: dict[str, SkillAttachment] = field(default_factory=dict)

    def register(self, attachment: SkillAttachment) -> None:
        self._skills[attachment.skill_id] = attachment

    def get(self, skill_id: str) -> SkillAttachment | None:
        return self._skills.get(str(skill_id).strip())

    def list(self) -> list[SkillAttachment]:
        return [self._skills[key] for key in sorted(self._skills)]
