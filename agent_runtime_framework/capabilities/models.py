from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CapabilitySpec:
    capability_id: str
    description: str
    intents: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    toolchains: list[list[str]] = field(default_factory=list)
    failure_signatures: list[str] = field(default_factory=list)
    verification_recipe: list[str] = field(default_factory=list)
    extension_policy: str = "reuse_only"

    @property
    def prerequisites(self) -> list[str]:
        return list(self.preconditions)

    def as_payload(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "description": self.description,
            "intents": list(self.intents),
            "preconditions": list(self.preconditions),
            "prerequisites": list(self.preconditions),
            "produces": list(self.produces),
            "toolchains": [list(chain) for chain in self.toolchains],
            "failure_signatures": list(self.failure_signatures),
            "verification_recipe": list(self.verification_recipe),
            "extension_policy": self.extension_policy,
        }


@dataclass(slots=True)
class CapabilityMacro:
    """组合已有能力链路的可复用配方（不新增原子工具权限）。"""

    recipe_id: str
    description: str
    intent_scope: list[str] = field(default_factory=list)
    entry_conditions: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    optional_capabilities: list[str] = field(default_factory=list)
    exit_conditions: list[str] = field(default_factory=list)
    fallback_recipes: list[str] = field(default_factory=list)
    verification_strategy: str = ""

    @property
    def macro_id(self) -> str:
        return self.recipe_id

    @property
    def capability_chain(self) -> list[str]:
        return list(self.required_capabilities)

    def as_payload(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "macro_id": self.recipe_id,
            "description": self.description,
            "intent_scope": list(self.intent_scope),
            "entry_conditions": list(self.entry_conditions),
            "required_capabilities": list(self.required_capabilities),
            "optional_capabilities": list(self.optional_capabilities),
            "exit_conditions": list(self.exit_conditions),
            "fallback_recipes": list(self.fallback_recipes),
            "verification_strategy": self.verification_strategy,
            "capability_chain": list(self.required_capabilities),
        }
