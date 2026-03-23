from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any, Protocol
from uuid import uuid4


@dataclass(slots=True)
class ArtifactRecord:
    artifact_id: str
    artifact_type: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ArtifactStore(Protocol):
    def add(
        self,
        artifact_type: str,
        *,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord: ...

    def list_recent(
        self,
        *,
        limit: int = 20,
        artifact_type: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> list[ArtifactRecord]: ...

    def cleanup_expired(self) -> int: ...


class InMemoryArtifactStore:
    def __init__(self, *, ttl_seconds: int | None = None) -> None:
        self._records: list[ArtifactRecord] = []
        self._ttl_seconds = ttl_seconds

    def add(
        self,
        artifact_type: str,
        *,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        record = ArtifactRecord(
            artifact_id=str(uuid4()),
            artifact_type=artifact_type,
            title=title,
            content=content,
            metadata=dict(metadata or {}),
        )
        self._records.append(record)
        return record

    def list_recent(
        self,
        *,
        limit: int = 20,
        artifact_type: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> list[ArtifactRecord]:
        self.cleanup_expired()
        if limit <= 0:
            return []
        filtered = [
            item
            for item in self._records
            if (artifact_type is None or item.artifact_type == artifact_type)
            and (run_id is None or str(item.metadata.get("run_id") or "") == run_id)
            and (task_id is None or str(item.metadata.get("task_id") or "") == task_id)
        ]
        return list(reversed(filtered[-limit:]))

    def cleanup_expired(self) -> int:
        if self._ttl_seconds is None or self._ttl_seconds <= 0:
            return 0
        now = datetime.now(timezone.utc)
        before = len(self._records)
        self._records = [
            item
            for item in self._records
            if (now - item.created_at).total_seconds() <= self._ttl_seconds
        ]
        return before - len(self._records)


class FileArtifactStore:
    def __init__(self, root: str | Path, *, ttl_seconds: int | None = None) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "index.jsonl"
        self._lock = threading.Lock()
        self._ttl_seconds = ttl_seconds
        if not self._index_path.exists():
            self._index_path.write_text("", encoding="utf-8")

    def add(
        self,
        artifact_type: str,
        *,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        record = ArtifactRecord(
            artifact_id=str(uuid4()),
            artifact_type=artifact_type,
            title=title,
            content=content,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self.cleanup_expired()
            content_path = self._root / f"{record.artifact_id}.txt"
            content_path.write_text(record.content, encoding="utf-8")
            payload = {
                "artifact_id": record.artifact_id,
                "artifact_type": record.artifact_type,
                "title": record.title,
                "content_path": str(content_path),
                "metadata": record.metadata,
                "created_at": record.created_at.isoformat(),
            }
            with self._index_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return record

    def list_recent(
        self,
        *,
        limit: int = 20,
        artifact_type: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> list[ArtifactRecord]:
        if limit <= 0:
            return []
        with self._lock:
            self.cleanup_expired()
            rows = self._load_rows()
        records: list[ArtifactRecord] = []
        for row in rows:
            if artifact_type is not None and str(row.get("artifact_type") or "") != artifact_type:
                continue
            metadata = dict(row.get("metadata") or {})
            if run_id is not None and str(metadata.get("run_id") or "") != run_id:
                continue
            if task_id is not None and str(metadata.get("task_id") or "") != task_id:
                continue
            content_path = Path(str(row.get("content_path") or "")).expanduser()
            content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
            created_at_raw = str(row.get("created_at") or "")
            created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else datetime.now(timezone.utc)
            records.append(
                ArtifactRecord(
                    artifact_id=str(row.get("artifact_id") or ""),
                    artifact_type=str(row.get("artifact_type") or ""),
                    title=str(row.get("title") or ""),
                    content=content,
                    metadata=metadata,
                    created_at=created_at,
                )
            )
        return list(reversed(records[-limit:]))

    def cleanup_expired(self) -> int:
        if self._ttl_seconds is None or self._ttl_seconds <= 0:
            return 0
        rows = self._load_rows()
        now = datetime.now(timezone.utc)
        kept: list[dict[str, Any]] = []
        deleted = 0
        for row in rows:
            created_at_raw = str(row.get("created_at") or "")
            created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else now
            if (now - created_at).total_seconds() <= self._ttl_seconds:
                kept.append(row)
                continue
            deleted += 1
            content_path = Path(str(row.get("content_path") or "")).expanduser()
            if content_path.exists():
                content_path.unlink()
        if deleted > 0:
            with self._index_path.open("w", encoding="utf-8") as handle:
                for row in kept:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return deleted

    def _load_rows(self) -> list[dict[str, Any]]:
        lines = self._index_path.read_text(encoding="utf-8").splitlines()
        rows: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows
