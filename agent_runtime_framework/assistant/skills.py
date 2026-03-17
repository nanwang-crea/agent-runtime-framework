from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


SkillRunner = Callable[[str, Any, Any], str]


@dataclass(slots=True)
class SkillSpec:
    name: str
    description: str
    runner: SkillRunner | None = None


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        *,
        runner: SkillRunner | None = None,
    ) -> None:
        self._skills[name] = SkillSpec(name=name, description=description, runner=runner)

    def get(self, name: str) -> SkillSpec | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return list(self._skills.keys())
