from __future__ import annotations

from pathlib import Path

from agent_runtime_framework.memory import (
    InMemoryIndexMemory,
    InMemorySessionMemory,
    WorkingMemory,
)
from agent_runtime_framework.policy import (
    PermissionLevel,
    PolicyDecision,
    SimpleDesktopPolicy,
)
from agent_runtime_framework.resources import ResourceRef


def test_session_memory_tracks_recent_focus():
    memory = InMemorySessionMemory()
    focus = ResourceRef.for_path(Path("/tmp/demo.txt"))

    memory.remember_focus([focus], summary="read demo")

    snapshot = memory.snapshot()

    assert snapshot.last_summary == "read demo"
    assert snapshot.focused_resources == [focus]


def test_working_memory_is_run_scoped():
    memory = WorkingMemory()

    memory.set("resolved_resources", ["a"])

    assert memory.get("resolved_resources") == ["a"]
    memory.clear()
    assert memory.get("resolved_resources") is None


def test_index_memory_caches_values():
    memory = InMemoryIndexMemory()

    memory.put("summary:/tmp/a", {"text": "cached"})

    assert memory.get("summary:/tmp/a") == {"text": "cached"}


def test_simple_desktop_policy_requires_confirmation_for_safe_write():
    policy = SimpleDesktopPolicy()

    decision = policy.authorize(PermissionLevel.SAFE_WRITE, confirmed=False)

    assert decision == PolicyDecision(
        allowed=True,
        requires_confirmation=True,
        reason="safe_write_requires_confirmation",
        safe_alternative="preview_only",
    )


def test_simple_desktop_policy_denies_destructive_write_by_default():
    policy = SimpleDesktopPolicy()

    decision = policy.authorize(PermissionLevel.DESTRUCTIVE_WRITE, confirmed=True)

    assert decision.allowed is False
    assert decision.reason == "destructive_write_disabled"
