from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(slots=True)
class CheckpointRecord:
    run_id: str
    session_id: str
    node_name: str
    status: str
    step_count: int
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CheckpointStore(Protocol):
    def save(self, record: CheckpointRecord) -> None: ...

    def list_for_run(self, run_id: str) -> list[CheckpointRecord]: ...

    def latest(self, run_id: str) -> CheckpointRecord | None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._runs: dict[str, list[CheckpointRecord]] = {}

    def save(self, record: CheckpointRecord) -> None:
        self._runs.setdefault(record.run_id, []).append(record)

    def list_for_run(self, run_id: str) -> list[CheckpointRecord]:
        return list(self._runs.get(run_id, []))

    def latest(self, run_id: str) -> CheckpointRecord | None:
        records = self._runs.get(run_id, [])
        if not records:
            return None
        return records[-1]
