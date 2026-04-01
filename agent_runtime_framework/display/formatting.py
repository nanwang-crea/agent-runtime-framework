from __future__ import annotations

from agent_runtime_framework.display.models import AgentDisplayProfile


def format_run_label(profile: AgentDisplayProfile, status: str) -> str:
    return f"[{profile.color}] {profile.label} ({status})"
