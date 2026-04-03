from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_runtime_framework.workflow.goal_intake import build_goal_envelope
from agent_runtime_framework.workflow.routing_runtime import RootGraphPayload, RuntimePayload


@dataclass(slots=True)
class AgentBranchOrchestrator:
    build_agent_graph_runtime: Callable[[], Any]
    build_runtime_context: Callable[[], dict[str, Any]]
    workflow_store: Any
    workflow_payload: Callable[[Any], dict[str, Any]]
    remember_workflow_run: Callable[[str, Any], None]
    capture_workflow_codex_history: Callable[[Any], None]
    application_context: Any
    workspace: Any
    context: Any
    get_pending_clarification: Callable[[], dict[str, Any] | None]
    record_run: Callable[[dict[str, Any], str], None]
    run_history_payload: Callable[[], list[dict[str, Any]]]

    def run(self, message: str, *, goal_spec: Any | None = None, root_graph: RootGraphPayload | None = None) -> RuntimePayload:
        runtime = self.build_agent_graph_runtime()
        goal_envelope = build_goal_envelope(message, application_context=self.application_context, workspace_root=self.workspace, context=self.context, goal_spec=goal_spec)
        prior_bundle = dict(self.get_pending_clarification() or {}) if self.get_pending_clarification() else None
        prior_run = None
        prior_state = None
        prior_graph = None
        if prior_bundle and prior_bundle.get("run_id"):
            try:
                prior_run = self.workflow_store.load(str(prior_bundle.get("run_id")))
            except Exception:
                prior_run = None
        if prior_run is not None:
            prior_state = dict(prior_run.metadata.get("agent_graph_state") or {})
            prior_graph = prior_run.graph
        run = runtime.run(goal_envelope, context=self.build_runtime_context(), prior_state=prior_state, prior_graph=prior_graph, clarification_response=(message if prior_run is not None else None))
        if root_graph is not None:
            run.metadata["root_graph"] = dict(root_graph)
        self.workflow_store.save(run)
        self.remember_workflow_run(message, run)
        self.capture_workflow_codex_history(run)
        payload = self.workflow_payload(run)
        self.record_run(payload, message)
        payload["run_history"] = self.run_history_payload()
        return payload
