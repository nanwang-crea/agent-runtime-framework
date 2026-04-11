from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CapabilitySpec:
    capability_id: str
    description: str
    intents: list[str] = field(default_factory=list)
    toolchains: list[list[str]] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    failure_signatures: list[str] = field(default_factory=list)
    verification_recipe: list[str] = field(default_factory=list)
    extension_policy: str = "reuse_only"

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CapabilityMacro:
    """组合已有能力链路的可复用配方（不新增原子工具权限）。"""

    macro_id: str
    description: str
    capability_chain: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)
