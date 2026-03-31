from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime_framework.memory import MemoryRecord


def remember_entity_binding(index_memory: Any, *, alias: str, path: str, entity_type: str, confidence: float) -> None:
    remember = getattr(index_memory, "remember", None)
    if not callable(remember):
        return
    normalized_alias = _normalize_alias(alias)
    if not normalized_alias or not path:
        return
    remember(
        MemoryRecord(
            key=f"entity:{normalized_alias}",
            text=f"{normalized_alias} maps to {path}",
            kind="entity_binding",
            metadata={
                "path": path,
                "layer": "entity",
                "record_kind": "entity_binding",
                "entity_type": entity_type,
                "alias": normalized_alias,
                "confidence": confidence,
                "retrievable_for_resolution": True,
            },
        )
    )


def search_entity_bindings(index_memory: Any, query: str, *, limit: int = 5) -> list[MemoryRecord]:
    search = getattr(index_memory, "search", None)
    if not callable(search):
        return []
    hits = list(search(query, limit=limit, kind="entity_binding"))
    hits.sort(key=lambda item: float(item.metadata.get("confidence") or 0.0), reverse=True)
    return hits


def aliases_for_path(path: str) -> list[str]:
    normalized = str(path or "").strip()
    if not normalized:
        return []
    name = Path(normalized).name
    stem = Path(normalized).stem
    aliases = [name, stem]
    if stem.lower() == "readme":
        aliases.extend(["README", "根目录 README", "readme"])
    return [alias for alias in dict.fromkeys(item.strip() for item in aliases if item.strip())]


def _normalize_alias(alias: str) -> str:
    normalized = str(alias or "").strip()
    return normalized
