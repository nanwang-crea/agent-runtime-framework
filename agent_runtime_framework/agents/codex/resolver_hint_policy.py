from __future__ import annotations

from agent_runtime_framework.memory import MemoryRecord
from agent_runtime_framework.resources import ResolveHint


def build_resolver_hints(records: list[MemoryRecord]) -> list[ResolveHint]:
    scored: list[tuple[float, ResolveHint]] = []
    seen: set[str] = set()
    for record in records:
        path = str(record.metadata.get("path") or "").strip()
        if not path or path in seen:
            continue
        if not _is_resolver_eligible(record):
            continue
        weight = _hint_weight(record)
        if weight <= 0:
            continue
        seen.add(path)
        scored.append(
            (
                weight,
                ResolveHint(
                    path=path,
                    source=str(record.kind or "memory"),
                    summary=str(record.metadata.get("summary") or record.text[:120]),
                ),
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [hint for _, hint in scored]


def _hint_weight(record: MemoryRecord) -> float:
    layer = str(record.metadata.get("layer") or "")
    path = str(record.metadata.get("path") or "").strip()
    confidence = float(record.metadata.get("confidence") or 0.0)
    if layer == "entity":
        return 100.0 + confidence
    if record.kind == "workspace_focus":
        return 90.0 + confidence
    if record.kind == "task_conclusion":
        return 40.0 + confidence
    if record.kind == "workspace_fact":
        return 20.0 + confidence
    if path == ".":
        return -1.0
    if layer == "core":
        return 60.0 + confidence
    if layer == "daily":
        return 10.0 + confidence
    return confidence


def _is_resolver_eligible(record: MemoryRecord) -> bool:
    if "retrievable_for_resolution" in record.metadata:
        return bool(record.metadata.get("retrievable_for_resolution"))
    path = str(record.metadata.get("path") or "").strip()
    if path == ".":
        return False
    if record.kind == "workspace_focus":
        return True
    if record.kind == "entity_binding":
        return True
    if record.kind == "task_conclusion":
        return True
    return False
