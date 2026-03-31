from __future__ import annotations

from dataclasses import dataclass
import re

from agent_runtime_framework.agents.codex.memory_schema import MemoryItem


@dataclass(slots=True)
class MemoryWriteDecision:
    allow_write: bool
    target_layer: str
    confidence: float
    retrievable_for_resolution: bool
    reason: str


def decide_memory_write(item: MemoryItem) -> MemoryWriteDecision:
    text = str(item.text or "").strip()
    path = str(item.path or "").strip()
    record_kind = str(item.record_kind or "")
    entity_type = str(item.entity_type or "unknown")
    if not text:
        return MemoryWriteDecision(False, "drop", 0.0, False, "empty_text")
    if record_kind == "entity_binding":
        confidence = max(float(item.confidence or 0.0), 0.95)
        return MemoryWriteDecision(True, "entity", confidence, True, "entity_binding")
    if _is_low_information_directory_summary(text, path=path):
        return MemoryWriteDecision(True, "daily", min(float(item.confidence or 0.0), 0.25), False, "low_information_summary")
    if record_kind in {"summary", "observation"} and path == ".":
        return MemoryWriteDecision(True, "daily", min(float(item.confidence or 0.0), 0.35), False, "workspace_root_summary")
    if entity_type == "file" and path and record_kind in {"observation", "summary"} and _looks_like_explicit_entity(text, path):
        return MemoryWriteDecision(True, "entity", max(float(item.confidence or 0.0), 0.9), True, "file_entity_binding")
    if record_kind in {"decision", "preference"}:
        return MemoryWriteDecision(True, "core", max(float(item.confidence or 0.0), 0.9), True, "durable_decision")
    return MemoryWriteDecision(True, item.layer or "daily", float(item.confidence or 0.4), bool(item.retrievable_for_resolution), "default")


def _is_low_information_directory_summary(text: str, *, path: str) -> bool:
    normalized = text.lower()
    if path != "." and "found " not in normalized:
        return False
    if re.fullmatch(r"found \d+ entries\.?", normalized):
        return True
    if "根据当前收集到的证据" in text and "Found " in text:
        return True
    return False


def _looks_like_explicit_entity(text: str, path: str) -> bool:
    basename = path.split("/")[-1]
    stem = basename.rsplit(".", 1)[0]
    haystack = text.lower()
    return basename.lower() in haystack or stem.lower() in haystack or stem == "README"
