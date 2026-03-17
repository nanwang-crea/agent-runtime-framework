from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


SkillRunner = Callable[[str, Any, Any], str]


@dataclass(slots=True)
class SkillSpec:
    name: str
    description: str
    runner: SkillRunner | None = None
    trigger_phrases: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    planner_hint: str | None = None


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        *,
        runner: SkillRunner | None = None,
        trigger_phrases: list[str] | None = None,
        required_capabilities: list[str] | None = None,
        planner_hint: str | None = None,
    ) -> None:
        self._skills[name] = SkillSpec(
            name=name,
            description=description,
            runner=runner,
            trigger_phrases=list(trigger_phrases or []),
            required_capabilities=list(required_capabilities or []),
            planner_hint=planner_hint,
        )

    def get(self, name: str) -> SkillSpec | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return list(self._skills.keys())

    def match_triggered(self, user_input: str) -> SkillSpec | None:
        lowered = user_input.lower()
        for skill in self._skills.values():
            for phrase in skill.trigger_phrases:
                if phrase.lower() in lowered:
                    return skill
        return None
