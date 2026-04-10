from __future__ import annotations

from typing import Any

from agent_runtime_framework.memory import TaskSnapshot, trim_task_snapshot
from agent_runtime_framework.workflow.state.models import (
    AgentGraphState,
    GoalEnvelope,
    SessionMemoryState,
    WorkingMemory,
    WorkflowMemoryState,
)


class WorkflowModelContextBuilder:
    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        items: list[str] = []
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        return items

    def _restore_memory_state(self, memory_state: AgentGraphState | WorkflowMemoryState | dict[str, Any] | None) -> WorkflowMemoryState:
        if isinstance(memory_state, AgentGraphState):
            return memory_state.memory_state
        if isinstance(memory_state, WorkflowMemoryState):
            return memory_state
        return WorkflowMemoryState.from_payload(dict(memory_state or {}))

    def _restore_graph_state(self, state: AgentGraphState | dict[str, Any] | None) -> AgentGraphState | None:
        if isinstance(state, AgentGraphState):
            return state
        return None

    def _session_memory(self, memory_state: AgentGraphState | WorkflowMemoryState | dict[str, Any] | None) -> SessionMemoryState:
        return self._restore_memory_state(memory_state).session_memory

    def _working_memory(self, memory_state: AgentGraphState | WorkflowMemoryState | dict[str, Any] | None) -> WorkingMemory:
        return self._restore_memory_state(memory_state).working_memory

    def _ineffective_actions(self, state: AgentGraphState | None) -> list[str]:
        if state is None:
            return []
        iteration_lookup = {
            int(item.get("iteration") or 0): str(item.get("planner_summary") or "").strip()
            for item in state.iteration_summaries
            if isinstance(item, dict)
        }
        return self._dedupe(
            [
                iteration_lookup.get(int(item.get("iteration") or 0), "")
                for item in state.failure_history[-2:]
                if isinstance(item, dict) and str(item.get("status") or "") != "accepted"
            ]
        )

    def build_task_snapshot_fragment(
        self,
        state_or_memory: AgentGraphState | WorkflowMemoryState | dict[str, Any] | None,
        *,
        goal: str | None = None,
        long_term_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self._restore_graph_state(state_or_memory)
        memory_state = self._restore_memory_state(state_or_memory)
        session_memory = memory_state.session_memory
        snapshot = trim_task_snapshot(
            TaskSnapshot(
                goal=str(goal or (state.goal_envelope.goal if state is not None else "")).strip(),
                recent_focus=[
                    item
                    for item in [session_memory.last_active_target, *list(session_memory.last_read_files)]
                    if str(item or "").strip()
                ],
                recent_paths=list(session_memory.recent_paths),
                last_action_summary=session_memory.last_action_summary,
                last_clarification=dict(session_memory.last_clarification)
                if isinstance(session_memory.last_clarification, dict)
                else None,
                long_term_hints=dict(
                    long_term_hints
                    if long_term_hints is not None
                    else (memory_state.long_term_memory if isinstance(memory_state.long_term_memory, dict) else {})
                ),
            )
        )
        return {
            "goal": snapshot.goal,
            "recent_focus": list(snapshot.recent_focus),
            "recent_paths": list(snapshot.recent_paths),
            "last_action_summary": snapshot.last_action_summary,
            "last_clarification": dict(snapshot.last_clarification) if isinstance(snapshot.last_clarification, dict) else None,
            "long_term_hints": dict(snapshot.long_term_hints),
        }

    def build_working_memory_fragment(
        self,
        state_or_memory: AgentGraphState | WorkflowMemoryState | dict[str, Any] | None,
        *,
        open_issues: list[str] | None = None,
        include_history: bool = True,
    ) -> dict[str, Any]:
        state = self._restore_graph_state(state_or_memory)
        working_memory = self._working_memory(state_or_memory)
        return {
            "active_target": working_memory.active_target,
            "confirmed_targets": list(working_memory.confirmed_targets),
            "excluded_targets": list(working_memory.excluded_targets),
            "current_step": working_memory.current_step,
            "open_issues": list(working_memory.open_issues or open_issues or (state.open_issues if state is not None else [])),
            "last_tool_result_summary": dict(working_memory.last_tool_result_summary)
            if isinstance(working_memory.last_tool_result_summary, dict)
            else None,
            "ineffective_actions": self._ineffective_actions(state) if include_history else [],
            "recent_failures": [dict(item) for item in state.failure_history[-2:] if isinstance(item, dict)] if include_history and state is not None else [],
            "recent_recovery": [dict(item) for item in state.recovery_history[-2:] if isinstance(item, dict)] if include_history and state is not None else [],
        }

    def build_response_context(self, memory_state: AgentGraphState | WorkflowMemoryState | dict[str, Any] | None) -> dict[str, Any]:
        task_snapshot = self.build_task_snapshot_fragment(memory_state, goal="", long_term_hints={})
        working_memory = self.build_working_memory_fragment(memory_state, include_history=False)
        return {
            "recent_focus": list(task_snapshot["recent_focus"]),
            "recent_paths": list(task_snapshot["recent_paths"]),
            "last_action_summary": task_snapshot["last_action_summary"],
            "last_clarification": dict(task_snapshot["last_clarification"]) if isinstance(task_snapshot["last_clarification"], dict) else None,
            "active_target": working_memory["active_target"],
            "confirmed_targets": list(working_memory["confirmed_targets"]),
            "excluded_targets": list(working_memory["excluded_targets"]),
        }

    def build_clarification_context(self, prior_state: dict[str, Any] | None) -> dict[str, Any]:
        memory_state = dict((prior_state or {}).get("memory_state") or {})
        task_snapshot = self.build_task_snapshot_fragment(memory_state, goal="", long_term_hints={})
        working_memory = self.build_working_memory_fragment(memory_state, include_history=False)
        return {
            "task_snapshot": {
                "recent_focus": list(task_snapshot["recent_focus"]),
                "recent_paths": list(task_snapshot["recent_paths"]),
                "last_action_summary": task_snapshot["last_action_summary"],
                "last_clarification": dict(task_snapshot["last_clarification"]) if isinstance(task_snapshot["last_clarification"], dict) else None,
            },
            "working_memory_view": {
                "active_target": working_memory["active_target"],
                "confirmed_targets": list(working_memory["confirmed_targets"]),
                "excluded_targets": list(working_memory["excluded_targets"]),
                "current_step": working_memory["current_step"],
                "open_issues": list(working_memory["open_issues"]),
                "last_tool_result_summary": dict(working_memory["last_tool_result_summary"])
                if isinstance(working_memory["last_tool_result_summary"], dict)
                else None,
            },
        }

    def build_planner_context(
        self,
        *,
        goal_envelope: GoalEnvelope,
        graph_state: AgentGraphState,
        latest_judge_decision: dict[str, Any] | None,
        execution_summary: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "goal": goal_envelope.goal,
            "intent": goal_envelope.intent,
            "target_hints": list(goal_envelope.target_hints),
            "success_criteria": list(goal_envelope.success_criteria),
            "iteration": graph_state.current_iteration + 1,
            "latest_judge_decision": dict(latest_judge_decision or {}) or None,
            "execution_summary": dict(execution_summary),
            "task_snapshot": self.build_task_snapshot_fragment(graph_state),
            "working_memory_view": self.build_working_memory_fragment(graph_state),
        }

    def build_judge_context(
        self,
        *,
        goal_envelope: GoalEnvelope,
        aggregated_payload: dict[str, Any],
        graph_state: AgentGraphState,
        execution_summary: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "goal": goal_envelope.goal,
            "intent": goal_envelope.intent,
            "target_hints": list(goal_envelope.target_hints),
            "success_criteria": list(goal_envelope.success_criteria),
            "constraints": dict(goal_envelope.constraints),
            "current_iteration": graph_state.current_iteration,
            "aggregated_payload": dict(aggregated_payload),
            "execution_summary": dict(execution_summary),
            "task_snapshot": self.build_task_snapshot_fragment(graph_state),
            "working_memory_view": self.build_working_memory_fragment(graph_state),
        }


DEFAULT_WORKFLOW_MODEL_CONTEXT_BUILDER = WorkflowModelContextBuilder()


__all__ = ["DEFAULT_WORKFLOW_MODEL_CONTEXT_BUILDER", "WorkflowModelContextBuilder"]
