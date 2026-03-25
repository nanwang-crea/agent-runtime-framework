from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class MemoryRecord:
    key: str
    text: str
    kind: str = "fact"
    metadata: dict[str, Any] = field(default_factory=dict)


class IndexMemory(Protocol):
    def get(self, key: str): ...

    def put(self, key: str, value: object) -> None: ...

    def remember(self, record: MemoryRecord) -> None: ...

    def search(self, query: str, *, limit: int = 5, kind: str | None = None) -> list[MemoryRecord]: ...


class InMemoryIndexMemory:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self._records: dict[str, MemoryRecord] = {}

    def get(self, key: str):
        return self._values.get(key)

    def put(self, key: str, value: object) -> None:
        self._values[key] = value

    def remember(self, record: MemoryRecord) -> None:
        normalized = _normalize_record(record)
        if not normalized.key or not normalized.text:
            return
        self._records[normalized.key] = normalized

    def search(self, query: str, *, limit: int = 5, kind: str | None = None) -> list[MemoryRecord]:
        query_tokens = _tokenize(query)
        if not query_tokens or limit <= 0:
            return []
        scored: list[tuple[int, int, MemoryRecord]] = []
        for index, record in enumerate(self._records.values()):
            if kind and record.kind != kind:
                continue
            score = _score_record(record, query_tokens)
            if score <= 0:
                continue
            scored.append((score, -index, record))
        scored.sort(reverse=True)
        return [record for _, _, record in scored[:limit]]


class MarkdownIndexMemory(InMemoryIndexMemory):
    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path).expanduser()
        self._values_path = self._path.with_name(f"{self._path.stem}.state.json")
        self._load_values()
        self._load_records()

    @property
    def path(self) -> Path:
        return self._path

    def put(self, key: str, value: object) -> None:
        normalized_key = str(key).strip()
        if not normalized_key:
            return
        if value is None:
            self._values.pop(normalized_key, None)
            self._persist_values()
            return
        serializable = _normalize_value(value)
        if serializable is None:
            return
        self._values[normalized_key] = serializable
        self._persist_values()

    def remember(self, record: MemoryRecord) -> None:
        normalized = _normalize_record(record)
        if not normalized.key or not normalized.text:
            return
        self._records[normalized.key] = normalized
        self._persist_records()

    def _load_values(self) -> None:
        if not self._values_path.exists():
            return
        try:
            parsed = json.loads(self._values_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(parsed, dict):
            self._values = {str(key): value for key, value in parsed.items()}

    def _load_records(self) -> None:
        if not self._path.exists():
            return
        self._records = _parse_markdown_records(self._path.read_text(encoding="utf-8"))

    def _persist_values(self) -> None:
        self._values_path.parent.mkdir(parents=True, exist_ok=True)
        self._values_path.write_text(
            json.dumps(self._values, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _persist_records(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(_render_markdown_records(self._records.values()), encoding="utf-8")


def _normalize_record(record: MemoryRecord) -> MemoryRecord:
    return MemoryRecord(
        key=record.key.strip(),
        text=record.text.strip(),
        kind=record.kind.strip() or "fact",
        metadata=dict(record.metadata or {}),
    )


def _normalize_value(value: object) -> Any | None:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return None


def _render_markdown_records(records: list[MemoryRecord] | Any) -> str:
    lines = [
        "# Agent Memory",
        "",
        "此文件由 Agent 自动维护，用于记录可检索的长期记忆。",
        "",
    ]
    for record in records:
        lines.append(f"## {record.kind} | {record.key}")
        lines.append(f"- text: {json.dumps(record.text, ensure_ascii=False)}")
        lines.append(f"- metadata: {json.dumps(record.metadata, ensure_ascii=False, sort_keys=True)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_markdown_records(text: str) -> dict[str, MemoryRecord]:
    records: dict[str, MemoryRecord] = {}
    current_kind = ""
    current_key = ""
    current_text = ""
    current_metadata: dict[str, Any] = {}

    def _flush() -> None:
        nonlocal current_kind, current_key, current_text, current_metadata
        if not current_key or not current_text:
            current_kind = ""
            current_key = ""
            current_text = ""
            current_metadata = {}
            return
        records[current_key] = MemoryRecord(
            key=current_key,
            text=current_text,
            kind=current_kind or "fact",
            metadata=dict(current_metadata),
        )
        current_kind = ""
        current_key = ""
        current_text = ""
        current_metadata = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            _flush()
            header = line[3:]
            kind, _, key = header.partition("|")
            current_kind = kind.strip()
            current_key = key.strip()
            continue
        if line.startswith("- text:"):
            payload = line[len("- text:") :].strip()
            current_text = _parse_markdown_payload(payload, default="")
            continue
        if line.startswith("- metadata:"):
            payload = line[len("- metadata:") :].strip()
            parsed = _parse_markdown_payload(payload, default={})
            current_metadata = parsed if isinstance(parsed, dict) else {}
    _flush()
    return records


def _parse_markdown_payload(payload: str, default: Any) -> Any:
    try:
        return json.loads(payload)
    except Exception:
        return default


def _score_record(record: MemoryRecord, query_tokens: list[str]) -> int:
    haystack = " ".join(
        [
            record.key,
            record.text,
            *(str(value) for value in record.metadata.values()),
        ]
    ).lower()
    haystack_tokens = set(_tokenize(haystack))
    score = 0
    for token in query_tokens:
        if token in haystack_tokens:
            score += 3
        elif token in haystack:
            score += 1
    return score


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_./-]+|[\u4e00-\u9fff]+", text.lower()) if token]
