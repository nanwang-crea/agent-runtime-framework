from __future__ import annotations

from dataclasses import dataclass

from agent_runtime_framework.capabilities.registry import CapabilityRegistry


@dataclass(slots=True)
class CapabilityExtensionRequest:
    """受控能力扩展请求（宏组合或受控适配层）。"""

    proposed_capability_id: str
    rationale: str
    extension_kind: str  # "macro" | "scoped_adapter"
    smoke_verification_recipe_id: str = "extension_smoke_default"


def assert_extension_preconditions(registry: CapabilityRegistry, request: CapabilityExtensionRequest) -> None:
    proposed = str(request.proposed_capability_id or "").strip()
    if not proposed:
        raise ValueError("proposed_capability_id is required")
    if registry.has(proposed):
        raise ValueError(f"capability already registered: {proposed}")
    kind = str(request.extension_kind or "").strip().lower()
    if kind not in {"macro", "scoped_adapter"}:
        raise ValueError("extension_kind must be macro or scoped_adapter")
