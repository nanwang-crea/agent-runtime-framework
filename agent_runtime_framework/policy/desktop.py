from __future__ import annotations

from agent_runtime_framework.policy.models import PermissionLevel, PolicyDecision


class SimpleDesktopPolicy:
    def authorize(self, permission_level: PermissionLevel, *, confirmed: bool = False) -> PolicyDecision:
        if permission_level in {PermissionLevel.METADATA_READ, PermissionLevel.CONTENT_READ}:
            return PolicyDecision(
                allowed=True,
                requires_confirmation=False,
                reason=f"{permission_level.value}_allowed",
            )
        if permission_level == PermissionLevel.SAFE_WRITE:
            if confirmed:
                return PolicyDecision(
                    allowed=True,
                    requires_confirmation=False,
                    reason="safe_write_confirmed",
                )
            return PolicyDecision(
                allowed=True,
                requires_confirmation=True,
                reason="safe_write_requires_confirmation",
                safe_alternative="preview_only",
            )
        return PolicyDecision(
            allowed=False,
            requires_confirmation=False,
            reason="destructive_write_disabled",
            safe_alternative="safe_write",
        )
