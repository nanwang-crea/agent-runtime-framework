from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DemoProfile:
    profile_id: str
    label: str
    kind: str = "agent"

    def to_payload(self) -> dict[str, str]:
        return {
            "id": self.profile_id,
            "label": self.label,
            "kind": self.kind,
        }


_BUILTIN_DEMO_PROFILES = [
    DemoProfile(profile_id="workspace", label="Workspace Agent"),
    DemoProfile(profile_id="qa_only", label="Q&A", kind="chat"),
    DemoProfile(profile_id="explore", label="Explore Agent"),
    DemoProfile(profile_id="plan", label="Plan Agent"),
    DemoProfile(profile_id="verification", label="Verification Agent"),
    DemoProfile(profile_id="conversation", label="Conversation Agent", kind="chat"),
]


def builtin_demo_profiles() -> list[DemoProfile]:
    return list(_BUILTIN_DEMO_PROFILES)


def get_demo_profile(profile_id: str) -> DemoProfile | None:
    needle = str(profile_id or "").strip()
    for profile in _BUILTIN_DEMO_PROFILES:
        if profile.profile_id == needle:
            return profile
    return None
