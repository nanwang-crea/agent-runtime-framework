from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime_framework.agents.codex.entity_memory import aliases_for_path
from agent_runtime_framework.agents.codex.memory_schema import MemoryItem


def extract_memory_items(task: Any, *, final_output: str) -> list[MemoryItem]:
    items: list[MemoryItem] = []
    now = datetime.now(timezone.utc).isoformat()
    task_id = str(getattr(task, "task_id", "task"))
    profile = str(getattr(task, "task_profile", "") or "")
    target_path = _target_path(task)
    if final_output.strip():
        items.append(
            MemoryItem(
                memory_id=f"task:{task_id}:conclusion",
                layer="daily",
                record_kind="summary",
                scope="path" if target_path else "session",
                text=final_output.strip(),
                path=target_path,
                entity_type=_entity_type_for_path(target_path),
                confidence=0.3,
                source_tool="answer_synthesizer",
                source_task_profile=profile,
                created_at=now,
            )
        )
    for index, fact in enumerate(getattr(getattr(task, "memory", None), "known_facts", [])[:5]):
        items.append(
            MemoryItem(
                memory_id=f"task:{task_id}:fact:{index}",
                layer="daily",
                record_kind="observation",
                scope="path" if target_path else "session",
                text=str(fact).strip(),
                path=target_path,
                entity_type=_entity_type_for_path(target_path),
                confidence=0.4,
                source_task_profile=profile,
                created_at=now,
            )
        )
    for alias in aliases_for_path(target_path):
        if not target_path:
            continue
        items.append(
            MemoryItem(
                memory_id=f"entity:{alias}",
                layer="entity",
                record_kind="entity_binding",
                scope="entity",
                text=f"{alias} maps to {target_path}",
                path=target_path,
                entity_name=alias,
                entity_type=_entity_type_for_path(target_path),
                confidence=0.98,
                source_task_profile=profile,
                created_at=now,
                retrievable_for_resolution=True,
            )
        )
    return items


def _target_path(task: Any) -> str:
    plan = getattr(task, "plan", None)
    if plan is not None:
        metadata = dict(getattr(plan, "metadata", {}) or {})
        path = str(metadata.get("resolved_path") or "").strip()
        if path:
            return path
    intent = getattr(task, "intent", None)
    return str(getattr(intent, "target_ref", "") or "").strip()


def _entity_type_for_path(path: str) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return "unknown"
    return "file" if Path(normalized).suffix else ("workspace" if normalized == "." else "directory")
