from __future__ import annotations

from typing import Any

from agent_runtime_framework.agents.codex.memory_extractor import extract_memory_items
from agent_runtime_framework.agents.codex.memory_policy import decide_memory_write
from agent_runtime_framework.agents.codex.models import CodexTask
from agent_runtime_framework.memory import MemoryRecord


def pending_clarification_payload(loop: Any, key: str) -> dict[str, Any] | None:
    index_memory = getattr(loop.context.application_context, "index_memory", None)
    get = getattr(index_memory, "get", None)
    if not callable(get):
        return None
    payload = get(key)
    return dict(payload) if isinstance(payload, dict) else None


def restore_persisted_pending_clarification(loop: Any, key: str, persisted_fields: tuple[str, ...]) -> CodexTask | None:
    payload = pending_clarification_payload(loop, key)
    if payload is None:
        return None
    goal = str(payload.get("goal") or "").strip()
    if not goal:
        return None
    task = CodexTask(
        goal=goal,
        actions=[],
        task_profile=str(payload.get("task_profile") or "chat").strip() or "chat",
        runtime_persona=str(payload.get("runtime_persona") or "").strip(),
    )
    memory_payload = dict(payload.get("memory") or {})
    for field_name, value in memory_payload.items():
        if field_name in persisted_fields and hasattr(task.state, field_name) and isinstance(value, list):
            setattr(task.state, field_name, [item for item in value if isinstance(item, (str, dict))])
    task.plan = loop._build_task_plan(task)
    return task


def store_persisted_pending_clarification(loop: Any, key: str, task: CodexTask, message: str, persisted_fields: tuple[str, ...]) -> None:
    index_memory = getattr(loop.context.application_context, "index_memory", None)
    put = getattr(index_memory, "put", None)
    if not callable(put):
        return
    put(
        key,
        {
            "goal": task.goal,
            "task_profile": task.task_profile,
            "runtime_persona": task.runtime_persona,
            "message": message,
            "memory": {field_name: list(getattr(task.state, field_name)) for field_name in persisted_fields},
        },
    )


def clear_persisted_pending_clarification(loop: Any, key: str) -> None:
    index_memory = getattr(loop.context.application_context, "index_memory", None)
    put = getattr(index_memory, "put", None)
    if callable(put):
        put(key, None)


def remember_completed_task(loop: Any, task: CodexTask, final_output: str) -> None:
    index_memory = getattr(loop.context.application_context, "index_memory", None)
    remember = getattr(index_memory, "remember", None)
    if not callable(remember):
        return
    target_path = completed_task_target_path(loop, task)
    relative_path = loop._relative_workspace_path(target_path) if target_path else ""
    for item in extract_memory_items(task, final_output=final_output):
        path = relative_path if item.path in {"", "."} else loop._relative_workspace_path(item.path)
        item.path = path or relative_path
        decision = decide_memory_write(item)
        if not decision.allow_write:
            continue
        remember(
            MemoryRecord(
                key=item.memory_id,
                text=f"{task.goal} {item.text}".strip(),
                kind="entity_binding" if decision.target_layer == "entity" else ("task_conclusion" if item.record_kind == "summary" else "workspace_fact"),
                metadata={
                    **item.as_metadata(),
                    "path": item.path,
                    "task_profile": task.task_profile,
                    "goal": task.goal,
                    "layer": decision.target_layer,
                    "confidence": decision.confidence,
                    "retrievable_for_resolution": decision.retrievable_for_resolution,
                },
            )
        )
    for index, claim in enumerate(task.state.typed_claims[:5]):
        detail = " ".join(
            str(claim.get(field) or "").strip()
            for field in ("subject", "detail", "kind")
            if str(claim.get(field) or "").strip()
        )
        if not detail:
            continue
        remember(
            MemoryRecord(
                key=f"task:{task.task_id}:typed:{index}",
                text=f"{task.goal} {detail}".strip(),
                kind="workspace_fact",
                metadata={
                    "path": relative_path,
                    "task_profile": task.task_profile,
                    "claim_kind": str(claim.get("kind") or ""),
                    "layer": "daily",
                    "record_kind": "observation",
                    "confidence": 0.5,
                    "retrievable_for_resolution": bool(claim.get("kind") == "role" and relative_path and relative_path != "."),
                },
            )
        )


def completed_task_target_path(loop: Any, task: CodexTask) -> str:
    plan = task.plan
    if plan is not None:
        if plan.target_semantics is not None and plan.target_semantics.path:
            return str(plan.target_semantics.path)
        resolved_path = str(plan.metadata.get("resolved_path") or "").strip()
        if resolved_path:
            return resolved_path
    if task.state.read_paths:
        return str(task.state.read_paths[-1])
    snapshot = loop.context.application_context.session_memory.snapshot()
    if snapshot.focused_resources:
        return str(snapshot.focused_resources[0].location)
    return ""
