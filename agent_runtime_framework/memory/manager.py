from __future__ import annotations

from typing import Any

from agent_runtime_framework.memory.task_snapshot import TaskSnapshot, trim_task_snapshot
from agent_runtime_framework.workflow.state.models import ConversationTurn, SessionMemoryState, WorkflowMemoryState, WorkingMemory


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


class MemoryManager:
    def update_session_memory(
        self,
        memory_state: WorkflowMemoryState,
        *,
        last_active_target: str | None = None,
        recent_paths: list[str] | None = None,
        last_action_summary: str | None = None,
        last_clarification: dict[str, Any] | None = None,
    ) -> None:
        session_memory = memory_state.session_memory
        if last_active_target is not None:
            session_memory.last_active_target = str(last_active_target).strip() or None
        if recent_paths is not None:
            session_memory.recent_paths = [str(item) for item in recent_paths if str(item).strip()]
        if last_action_summary is not None:
            session_memory.last_action_summary = str(last_action_summary).strip() or None
        if last_clarification is not None:
            session_memory.last_clarification = dict(last_clarification) or None

    def update_working_memory(
        self,
        memory_state: WorkflowMemoryState,
        *,
        active_target: str | None = None,
        confirmed_targets: list[str] | None = None,
        excluded_targets: list[str] | None = None,
        current_step: str | None = None,
    ) -> None:
        working_memory = memory_state.working_memory
        if active_target is not None:
            working_memory.active_target = str(active_target).strip() or None
        if confirmed_targets is not None:
            working_memory.confirmed_targets = [str(item) for item in confirmed_targets if str(item).strip()]
        if excluded_targets is not None:
            working_memory.excluded_targets = [str(item) for item in excluded_targets if str(item).strip()]
        if current_step is not None:
            working_memory.current_step = str(current_step).strip() or None

    def build_task_snapshot(
        self,
        *,
        session_memory: SessionMemoryState,
        long_term_memory: dict[str, Any] | None,
        transcript: list[ConversationTurn],
    ) -> TaskSnapshot:
        goal = ""
        for turn in reversed(transcript):
            if str(turn.role).strip() == "user" and str(turn.content).strip():
                goal = str(turn.content).strip()
                break
        if not goal and transcript:
            goal = str(transcript[-1].content or "").strip()

        recent_focus = _dedupe([session_memory.last_active_target, *list(session_memory.last_read_files)])
        snapshot = TaskSnapshot(
            goal=goal,
            recent_focus=recent_focus,
            recent_paths=_dedupe(list(session_memory.recent_paths)),
            last_action_summary=session_memory.last_action_summary,
            last_clarification=dict(session_memory.last_clarification)
            if isinstance(session_memory.last_clarification, dict)
            else None,
            long_term_hints=dict(long_term_memory or {}),
        )
        return trim_task_snapshot(snapshot)

    def init_working_memory(self, snapshot: TaskSnapshot) -> WorkingMemory:
        active_target = next(
            (item for item in [*list(snapshot.recent_focus), *list(snapshot.recent_paths)] if str(item).strip()),
            None,
        )
        return WorkingMemory(
            active_target=active_target,
            confirmed_targets=[active_target] if active_target else [],
            excluded_targets=[],
            current_step=str(snapshot.goal).strip() or None,
            open_issues=[],
            last_tool_result_summary=None,
        )

    def checkpoint_working_memory(self, working_memory: WorkingMemory) -> dict[str, Any]:
        return working_memory.as_payload()

    def restore_working_memory(self, payload: dict[str, Any]) -> WorkingMemory:
        data = dict(payload or {})
        return WorkingMemory(
            active_target=str(data.get("active_target")).strip() if data.get("active_target") else None,
            confirmed_targets=[str(item) for item in data.get("confirmed_targets", []) or [] if str(item).strip()],
            excluded_targets=[str(item) for item in data.get("excluded_targets", []) or [] if str(item).strip()],
            current_step=str(data.get("current_step")).strip() if data.get("current_step") else None,
            open_issues=[str(item) for item in data.get("open_issues", []) or [] if str(item).strip()],
            last_tool_result_summary=dict(data.get("last_tool_result_summary") or {})
            if isinstance(data.get("last_tool_result_summary"), dict)
            else None,
        )

    def validate_working_memory(
        self,
        working_memory: WorkingMemory,
        *,
        session_memory: SessionMemoryState,
    ) -> WorkingMemory:
        recent_paths = {str(item).strip() for item in session_memory.recent_paths if str(item).strip()}
        active_target = str(working_memory.active_target or "").strip()
        if active_target and recent_paths and active_target not in recent_paths:
            return WorkingMemory(
                active_target=None,
                confirmed_targets=[],
                excluded_targets=list(working_memory.excluded_targets),
                current_step=None,
                open_issues=list(working_memory.open_issues),
                last_tool_result_summary=working_memory.last_tool_result_summary,
            )
        return working_memory

    def update_session_from_tool_result(
        self,
        session_memory: SessionMemoryState,
        result: dict[str, Any],
    ) -> SessionMemoryState:
        updated = SessionMemoryState(
            last_active_target=session_memory.last_active_target,
            recent_paths=list(session_memory.recent_paths),
            last_action_summary=session_memory.last_action_summary,
            last_read_files=list(session_memory.last_read_files),
            last_clarification=dict(session_memory.last_clarification)
            if isinstance(session_memory.last_clarification, dict)
            else None,
        )
        path = str((result or {}).get("path") or "").strip()
        summary = str((result or {}).get("summary") or "").strip()
        if path:
            updated.last_active_target = path
            updated.recent_paths = _dedupe([path, *updated.recent_paths])
        if summary:
            updated.last_action_summary = summary
        return updated

    def update_session_from_clarification(
        self,
        session_memory: SessionMemoryState,
        clarification: dict[str, Any],
    ) -> SessionMemoryState:
        return SessionMemoryState(
            last_active_target=session_memory.last_active_target,
            recent_paths=list(session_memory.recent_paths),
            last_action_summary=session_memory.last_action_summary,
            last_read_files=list(session_memory.last_read_files),
            last_clarification=dict(clarification or {}) or None,
        )

    def update_session_from_final_response(
        self,
        session_memory: SessionMemoryState,
        response: dict[str, Any],
    ) -> SessionMemoryState:
        return SessionMemoryState(
            last_active_target=session_memory.last_active_target,
            recent_paths=list(session_memory.recent_paths),
            last_action_summary=str((response or {}).get("summary") or "").strip() or session_memory.last_action_summary,
            last_read_files=list(session_memory.last_read_files),
            last_clarification=dict(session_memory.last_clarification)
            if isinstance(session_memory.last_clarification, dict)
            else None,
        )

    def update_long_term_if_needed(
        self,
        long_term_memory: dict[str, Any] | None,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        updated = dict(long_term_memory or {})
        memory_hint = dict((event or {}).get("memory_hint") or {})
        if not memory_hint:
            return updated
        scope = str(memory_hint.get("scope") or "").strip()
        if scope == "project_conventions":
            updated.setdefault("project_conventions", {}).update(dict(memory_hint.get("values") or {}))
        elif scope == "user_preferences":
            updated.setdefault("user_preferences", {}).update(dict(memory_hint.get("values") or {}))
        elif scope == "path_aliases":
            updated.setdefault("path_aliases", {}).update(dict(memory_hint.get("values") or {}))
        return updated


__all__ = ["MemoryManager"]
