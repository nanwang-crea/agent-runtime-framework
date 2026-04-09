from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _dedupe_trimmed(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= limit:
            break
    return items


@dataclass(slots=True)
class TaskSnapshot:
    goal: str
    recent_focus: list[str] = field(default_factory=list)
    recent_paths: list[str] = field(default_factory=list)
    last_action_summary: str | None = None
    last_clarification: dict[str, Any] | None = None
    long_term_hints: dict[str, Any] = field(default_factory=dict)


def trim_task_snapshot(
    snapshot: TaskSnapshot,
    *,
    max_recent_focus: int = 5,
    max_recent_paths: int = 10,
) -> TaskSnapshot:
    return TaskSnapshot(
        goal=str(snapshot.goal or "").strip(),
        recent_focus=_dedupe_trimmed(list(snapshot.recent_focus), limit=max_recent_focus),
        recent_paths=_dedupe_trimmed(list(snapshot.recent_paths), limit=max_recent_paths),
        last_action_summary=str(snapshot.last_action_summary).strip() if snapshot.last_action_summary else None,
        last_clarification=dict(snapshot.last_clarification) if isinstance(snapshot.last_clarification, dict) else None,
        long_term_hints=dict(snapshot.long_term_hints or {}),
    )


__all__ = ["TaskSnapshot", "trim_task_snapshot"]
