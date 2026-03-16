from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PermissionLevel(str, Enum):
    METADATA_READ = "metadata_read"
    CONTENT_READ = "content_read"
    SAFE_WRITE = "safe_write"
    DESTRUCTIVE_WRITE = "destructive_write"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str
    safe_alternative: str | None = None
