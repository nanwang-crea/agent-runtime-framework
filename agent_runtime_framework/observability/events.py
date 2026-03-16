from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RunEvent:
    stage: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)
