from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: str
    label: str
    kind: str = "agent"

    def to_payload(self) -> dict[str, str]:
        return {"id": self.profile_id, "label": self.label, "kind": self.kind}


_BUILTIN_PROFILES = [
    Profile(profile_id="workspace", label="Workspace Agent"),
    Profile(profile_id="qa_only", label="Q&A", kind="chat"),
    Profile(profile_id="explore", label="Explore Agent"),
    Profile(profile_id="plan", label="Plan Agent"),
    Profile(profile_id="verification", label="Verification Agent"),
    Profile(profile_id="conversation", label="Conversation Agent", kind="chat"),
]


def builtin_profiles() -> list[Profile]:
    return list(_BUILTIN_PROFILES)


def get_profile(profile_id: str) -> Profile | None:
    needle = str(profile_id or "").strip()
    for profile in _BUILTIN_PROFILES:
        if profile.profile_id == needle:
            return profile
    return None
