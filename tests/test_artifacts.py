from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from agent_runtime_framework.artifacts import FileArtifactStore, InMemoryArtifactStore


def test_in_memory_artifact_store_supports_filters():
    store = InMemoryArtifactStore()
    store.add("change_summary", title="a", content="1", metadata={"run_id": "run-1", "task_id": "t1"})
    store.add("rollback_checkpoint", title="b", content="2", metadata={"run_id": "run-1", "task_id": "t2"})
    store.add("change_summary", title="c", content="3", metadata={"run_id": "run-2", "task_id": "t1"})

    filtered = store.list_recent(limit=10, artifact_type="change_summary", run_id="run-1")

    assert len(filtered) == 1
    assert filtered[0].title == "a"


def test_in_memory_artifact_store_ttl_cleanup():
    store = InMemoryArtifactStore(ttl_seconds=1)
    fresh = store.add("change_summary", title="fresh", content="ok")
    expired = store.add("change_summary", title="expired", content="old")
    expired.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    removed = store.cleanup_expired()

    assert removed == 1
    records = store.list_recent(limit=10)
    assert len(records) == 1
    assert records[0].artifact_id == fresh.artifact_id


def test_file_artifact_store_supports_concurrent_writes(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")

    def _write_one(index: int) -> None:
        store.add(
            "change_summary",
            title=f"title-{index}",
            content=f"content-{index}",
            metadata={"run_id": "run-1", "task_id": f"t{index}"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_write_one, range(50)))

    records = store.list_recent(limit=100, artifact_type="change_summary", run_id="run-1")
    assert len(records) == 50
