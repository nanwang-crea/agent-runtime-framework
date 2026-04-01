from __future__ import annotations

from agent_runtime_framework.display.models import AgentDisplayProfile


_PALETTE = ("blue", "green", "purple", "orange", "red", "teal")


def color_for_agent(agent_id: str) -> str:
    normalized = str(agent_id or "").strip()
    if not normalized:
        return _PALETTE[0]
    return _PALETTE[sum(ord(ch) for ch in normalized) % len(_PALETTE)]


def build_display_profile(agent_id: str, label: str) -> AgentDisplayProfile:
    return AgentDisplayProfile(agent_id=agent_id, label=label, color=color_for_agent(agent_id))
