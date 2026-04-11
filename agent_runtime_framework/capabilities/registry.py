from __future__ import annotations

from typing import Any

from agent_runtime_framework.capabilities.models import CapabilityMacro, CapabilitySpec

_default_registry: Any | None = None


class CapabilityRegistry:
    def __init__(
        self,
        specs: dict[str, CapabilitySpec] | None = None,
        recipes: dict[str, CapabilityMacro] | None = None,
    ) -> None:
        self._specs: dict[str, CapabilitySpec] = dict(specs or {})
        self._recipes: dict[str, CapabilityMacro] = dict(recipes or {})

    def register(self, spec: CapabilitySpec) -> None:
        if spec.capability_id in self._specs:
            raise ValueError(f"duplicate capability_id: {spec.capability_id}")
        self._specs[spec.capability_id] = spec

    def register_recipe(self, recipe: CapabilityMacro) -> None:
        if recipe.recipe_id in self._recipes:
            raise ValueError(f"duplicate recipe_id: {recipe.recipe_id}")
        self._recipes[recipe.recipe_id] = recipe

    def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._specs.get(capability_id)

    def get_recipe(self, recipe_id: str) -> CapabilityMacro | None:
        return self._recipes.get(recipe_id)

    def has(self, capability_id: str) -> bool:
        return capability_id in self._specs

    def has_recipe(self, recipe_id: str) -> bool:
        return recipe_id in self._recipes

    def list_specs(self) -> list[CapabilitySpec]:
        return list(self._specs.values())

    def list_recipes(self) -> list[CapabilityMacro]:
        return list(self._recipes.values())

    def list_payloads(self) -> list[dict[str, Any]]:
        return [spec.as_payload() for spec in self._specs.values()]

    def list_recipe_payloads(self) -> list[dict[str, Any]]:
        return [recipe.as_payload() for recipe in self._recipes.values()]

    def match_failure(self, failure_diagnosis: dict[str, Any] | None) -> list[str]:
        if not failure_diagnosis:
            return []
        haystack = " ".join(
            [
                str(failure_diagnosis.get("category") or ""),
                str(failure_diagnosis.get("subcategory") or ""),
                str(failure_diagnosis.get("summary") or ""),
                str(failure_diagnosis.get("blocking_issue") or ""),
            ]
        ).lower()
        matched: list[str] = []
        for spec in self._specs.values():
            for sig in spec.failure_signatures:
                token = str(sig or "").strip().lower()
                if token and token in haystack and spec.capability_id not in matched:
                    matched.append(spec.capability_id)
        return matched


def get_default_capability_registry() -> CapabilityRegistry:
    global _default_registry
    if _default_registry is None:
        from agent_runtime_framework.capabilities.defaults import build_default_capability_registry

        _default_registry = build_default_capability_registry()
    return _default_registry


def resolve_capability_registry(services: dict[str, Any] | None) -> CapabilityRegistry:
    if isinstance(services, dict):
        reg = services.get("capability_registry")
        if isinstance(reg, CapabilityRegistry):
            return reg
    return get_default_capability_registry()
