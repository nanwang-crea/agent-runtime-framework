"""Permission levels and default desktop policy decisions."""

from agent_runtime_framework.policy.desktop import SimpleDesktopPolicy
from agent_runtime_framework.policy.models import PermissionLevel, PolicyDecision

__all__ = [
    "PermissionLevel",
    "PolicyDecision",
    "SimpleDesktopPolicy",
]
