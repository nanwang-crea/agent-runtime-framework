from __future__ import annotations

from typing import Any

from agent_runtime_framework.api.responses.common_payloads import compact_text, resource_payload


def build_context_payload(
    *,
    workspace: str,
    active_persona: str,
    available_workspaces: list[str],
    sandbox_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "active_persona": active_persona,
        "active_workspace": workspace,
        "available_workspaces": list(dict.fromkeys([workspace, *available_workspaces])),
        "sandbox": sandbox_payload,
    }


def build_session_payload(session: Any) -> dict[str, Any]:
    if session is None:
        return {"session_id": None, "turns": []}
    return {
        "session_id": session.session_id,
        "turns": [{"role": turn.role, "content": turn.content} for turn in session.turns],
    }


def build_memory_payload(*, session: Any, session_memory: Any) -> dict[str, Any]:
    snapshot = session_memory.snapshot()
    focused_resources = list(snapshot.focused_resources)
    return {
        "focused_resource": resource_payload(focused_resources[0]) if focused_resources else None,
        "recent_resources": [resource_payload(resource) for resource in focused_resources[:5]],
        "last_summary": snapshot.last_summary,
        "active_capability": session.focused_capability if session is not None else None,
    }


def build_plan_history_payload(tasks: list[Any], *, limit: int = 40) -> list[dict[str, Any]]:
    return [
        {
            "plan_id": task.task_id,
            "goal": task.goal,
            "steps": [
                {
                    "capability_name": action.kind,
                    "instruction": action.instruction,
                    "status": action.status,
                    "observation": compact_text(action.observation),
                }
                for action in task.actions
            ],
        }
        for task in reversed(tasks[:limit])
    ]


def build_run_history_payload(run_history: list[dict[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    return list(run_history[:limit])
