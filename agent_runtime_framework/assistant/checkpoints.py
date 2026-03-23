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

    def link_artifacts(self, run_id: str, task_id: str, artifact_ids: list[str]) -> None: ...

    def artifacts_for_run(self, run_id: str) -> dict[str, list[str]]: ...

    def replay_input(self, run_id: str) -> str | None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._runs: dict[str, list[CheckpointRecord]] = {}
        self._artifact_index: dict[str, dict[str, list[str]]] = {}

    def save(self, record: CheckpointRecord) -> None:
        self._runs.setdefault(record.run_id, []).append(record)

    def list_for_run(self, run_id: str) -> list[CheckpointRecord]:
        return list(self._runs.get(run_id, []))

    def latest(self, run_id: str) -> CheckpointRecord | None:
        records = self._runs.get(run_id, [])
        if not records:
            return None
        return records[-1]

    def link_artifacts(self, run_id: str, task_id: str, artifact_ids: list[str]) -> None:
        if not artifact_ids:
            return
        run_index = self._artifact_index.setdefault(run_id, {})
        task_index = run_index.setdefault(task_id, [])
        for artifact_id in artifact_ids:
            if artifact_id not in task_index:
                task_index.append(artifact_id)

    def artifacts_for_run(self, run_id: str) -> dict[str, list[str]]:
        run_index = self._artifact_index.get(run_id, {})
        return {task_id: list(ids) for task_id, ids in run_index.items()}

    def replay_input(self, run_id: str) -> str | None:
        for record in self._runs.get(run_id, []):
            goal = str(record.payload.get("goal") or "")
            if goal:
                return goal
        return None
