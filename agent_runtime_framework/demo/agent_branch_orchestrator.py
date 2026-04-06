from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from agent_runtime_framework.workflow.clarification_interpreter import resolve_clarification_response
from agent_runtime_framework.workflow.goal_intake import build_goal_envelope
from agent_runtime_framework.workflow.routing_runtime import RootGraphPayload, RuntimePayload


@dataclass(slots=True)
class AgentBranchOrchestrator:
    build_agent_graph_runtime: Callable[[], Any]
    build_runtime_context: Callable[[], dict[str, Any]]
    workflow_store: Any
    workflow_payload: Callable[[Any], dict[str, Any]]
    remember_workflow_run: Callable[[str, Any], None]
    application_context: Any
    workspace: Any
    context: Any
    get_pending_clarification: Callable[[], dict[str, Any] | None]
    record_run: Callable[[dict[str, Any], str], None]
    run_history_payload: Callable[[], list[dict[str, Any]]]

    def run(self, message: str, *, goal_spec: Any | None = None, root_graph: RootGraphPayload | None = None) -> RuntimePayload:
        runtime = self.build_agent_graph_runtime()
        prior_bundle = dict(self.get_pending_clarification() or {}) if self.get_pending_clarification() else None
        prior_run = None
        prior_state = None
        prior_graph = None
        clarification_resolution = None
        if prior_bundle and prior_bundle.get("run_id"):
            try:
                prior_run = self.workflow_store.load(str(prior_bundle.get("run_id")))
            except Exception:
                prior_run = None
        if prior_run is not None:
            prior_state = dict(prior_run.metadata.get("agent_graph_state") or {})
            prior_graph = prior_run.graph
        if prior_run is not None:
            prior_goal_envelope = dict(prior_run.metadata.get("goal_envelope") or {})
            clarification_resolution = resolve_clarification_response(
                self.build_runtime_context(),
                prior_goal_envelope=prior_goal_envelope,
                pending_request=prior_bundle or {},
                user_response=message,
                prior_state=prior_state,
            )
            if (
                str(clarification_resolution.get("confirmed_target") or "").strip()
                and not bool(clarification_resolution.get("should_reask"))
                and "confirmed" not in clarification_resolution
            ):
                clarification_resolution = {**dict(clarification_resolution), "confirmed": True}
            updated_hints = list(dict.fromkeys([*list(prior_goal_envelope.get("target_hints") or []), *list(clarification_resolution.get("updated_target_hints") or [])]))
            prior_goal_envelope["target_hints"] = updated_hints
            memory_state = dict((prior_state or {}).get("memory_state") or {})
            semantic_memory = dict(memory_state.get("semantic_memory") or {})
            clarification_memory = dict(memory_state.get("clarification_memory") or {})
            if confirmed_target := str(clarification_resolution.get("confirmed_target") or "").strip():
                semantic_memory["confirmed_targets"] = [confirmed_target]
            if excluded_targets := [str(item) for item in clarification_resolution.get("excluded_targets", []) or [] if str(item).strip()]:
                semantic_memory["excluded_targets"] = excluded_targets
            if str(clarification_resolution.get("preferred_path") or "").strip():
                semantic_memory["interpreted_target"] = {
                    "target_kind": "file",
                    "preferred_path": str(clarification_resolution.get("preferred_path") or "").strip(),
                    "scope_preference": "any",
                    "exclude_paths": [str(item) for item in clarification_resolution.get("excluded_targets", []) or [] if str(item).strip()],
                    "confirmed": bool(clarification_resolution.get("confirmed")),
                    "confidence": float(clarification_resolution.get("confidence") or 0.8),
                }
            clarification_memory["last_resolution"] = dict(clarification_resolution)
            memory_state["semantic_memory"] = semantic_memory
            memory_state["clarification_memory"] = clarification_memory
            prior_state = {**dict(prior_state or {}), "memory_state": memory_state}
            goal_envelope = build_goal_envelope(
                str(prior_goal_envelope.get("goal") or message),
                application_context=self.application_context,
                workspace_root=self.workspace,
                context=self.context,
                goal_spec=SimpleNamespace(
                    original_goal=str(prior_goal_envelope.get("goal") or message),
                    primary_intent=str(prior_goal_envelope.get("intent") or "file_read"),
                    requires_target_interpretation=False,
                    requires_search=False,
                    requires_read=True,
                    requires_verification=False,
                    metadata={"target_hint": updated_hints[0]} if updated_hints else {},
                ),
            )
        else:
            goal_envelope = build_goal_envelope(message, application_context=self.application_context, workspace_root=self.workspace, context=self.context, goal_spec=goal_spec)
        run = runtime.run(
            goal_envelope,
            context=self.build_runtime_context(),
            prior_state=prior_state,
            prior_graph=prior_graph,
            clarification_response=(message if prior_run is not None else None),
            clarification_resolution=clarification_resolution,
        )
        if root_graph is not None:
            run.metadata["root_graph"] = dict(root_graph)
        self.workflow_store.save(run)
        self.remember_workflow_run(message, run)
        payload = self.workflow_payload(run)
        self.record_run(payload, message)
        payload["run_history"] = self.run_history_payload()
        return payload
