from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_runtime_framework.api.process_trace import emit_process_event, process_event_for_node
from agent_runtime_framework.workflow.state.approval import WorkflowResumeToken, create_resume_token
from agent_runtime_framework.workflow.context.runtime_context import WorkflowRuntimeContext
from agent_runtime_framework.workflow.state.models import (
    NODE_STATUS_FAILED,
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    NODE_STATUS_WAITING_APPROVAL,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_WAITING_APPROVAL,
    RUN_STATUS_WAITING_INPUT,
    NodeResult,
    WorkflowRun,
)
from agent_runtime_framework.workflow.runtime.protocols import ResumableWorkflowNodeExecutor, WorkflowNodeExecutor
from agent_runtime_framework.workflow.runtime.scheduler import WorkflowScheduler


@dataclass(slots=True)
class GraphExecutionRuntime:
    executors: dict[str, WorkflowNodeExecutor]
    scheduler: WorkflowScheduler = field(default_factory=WorkflowScheduler)
    context: WorkflowRuntimeContext = field(default_factory=WorkflowRuntimeContext)
    process_sink: Callable[[dict[str, Any]], None] | None = None

    def run(self, run: WorkflowRun) -> WorkflowRun:
        run.shared_state.setdefault("node_results", {})
        run.metadata.setdefault("process_events", [])
        for node in run.graph.nodes:
            run.node_states.setdefault(node.node_id, self._make_state(node.node_id))

        run.status = RUN_STATUS_RUNNING
        while True:
            ready_nodes = self.scheduler.ready_nodes(run)
            if not ready_nodes:
                if any(state.status == NODE_STATUS_FAILED for state in run.node_states.values()):
                    run.status = RUN_STATUS_FAILED
                elif any(state.status == NODE_STATUS_WAITING_APPROVAL for state in run.node_states.values()):
                    run.status = RUN_STATUS_WAITING_APPROVAL
                elif run.pending_interaction is not None:
                    run.status = RUN_STATUS_WAITING_INPUT
                else:
                    run.status = RUN_STATUS_COMPLETED
                return run

            for node in ready_nodes:
                state = run.node_states[node.node_id]
                if node.requires_approval and state.approval_granted is not True:
                    token = create_resume_token(node.node_id)
                    state.status = NODE_STATUS_WAITING_APPROVAL
                    state.approval_requested = True
                    run.shared_state["resume_token"] = token
                    self._emit(run, {"kind": "approval", "status": "started", "title": "等待审批", "detail": str(node.metadata.get("approval_summary") or "需要审批后继续"), "node_id": node.node_id, "node_type": node.node_type})
                    run.status = RUN_STATUS_WAITING_APPROVAL
                    return run

                state.status = NODE_STATUS_RUNNING
                self._emit_node(run, node, state.status)
                executor = self.executors.get(node.node_type)
                if executor is None:
                    state.status = NODE_STATUS_FAILED
                    state.error = f"No executor registered for node type: {node.node_type}"
                    state.result = NodeResult(status=NODE_STATUS_FAILED, error=state.error)
                    run.shared_state["node_results"][node.node_id] = state.result
                    self._emit_node(run, node, state.status, state.result)
                    run.status = RUN_STATUS_FAILED
                    return run

                result = self._execute(executor, node, run)
                state.result = result
                state.error = result.error
                state.status = result.status
                run.shared_state["node_results"][node.node_id] = result
                self._emit_node(run, node, state.status, result)
                if result.status == NODE_STATUS_FAILED:
                    run.status = RUN_STATUS_FAILED
                    return run
                if result.status == NODE_STATUS_WAITING_APPROVAL:
                    state.approval_requested = True
                    run.shared_state["resume_token"] = create_resume_token(node.node_id)
                    self._emit(run, {"kind": "approval", "status": "started", "title": "等待审批", "detail": str(result.error or getattr(result.interaction_request, 'summary', '') or "需要审批后继续"), "node_id": node.node_id, "node_type": node.node_type})
                    run.status = RUN_STATUS_WAITING_APPROVAL
                    return run
                if result.interaction_request is not None:
                    if result.interaction_request.source_node_id is None:
                        result.interaction_request.source_node_id = node.node_id
                    run.pending_interaction = result.interaction_request
                    self._emit(run, {"kind": "approval", "status": "started", "title": str(result.interaction_request.summary or "等待输入"), "detail": str(result.interaction_request.prompt or "").strip() or None, "node_id": node.node_id, "node_type": node.node_type})
                    run.status = RUN_STATUS_WAITING_INPUT
                    return run

    def resume(self, run: WorkflowRun, *, resume_token: WorkflowResumeToken, approved: bool) -> WorkflowRun:
        pending = run.shared_state.get("resume_token")
        if pending is None or pending.token_id != resume_token.token_id:
            run.status = RUN_STATUS_FAILED
            run.error = "invalid resume token"
            return run

        state = run.node_states[resume_token.node_id]
        executor = self.executors.get(next(node.node_type for node in run.graph.nodes if node.node_id == resume_token.node_id), None)
        approval_kind = str((state.result.approval_data if state.result is not None else {}).get("kind") or "")
        if approval_kind:
            if executor is None or not hasattr(executor, "resume"):
                state.status = NODE_STATUS_FAILED
                state.error = f"Node executor cannot resume approval for node: {resume_token.node_id}"
                run.status = RUN_STATUS_FAILED
                return run
            node = next(node for node in run.graph.nodes if node.node_id == resume_token.node_id)
            result = executor.resume(node, run, state.result, approved=approved, context=self.context)  # type: ignore[union-attr]
            state.approval_granted = approved
            state.result = result
            state.error = result.error
            state.status = result.status
            run.shared_state["node_results"][node.node_id] = result
            run.shared_state.pop("resume_token", None)
            if result.status == NODE_STATUS_FAILED:
                run.status = RUN_STATUS_FAILED
                return run
            return self.run(run)

        state.approval_granted = approved
        if not approved:
            state.status = NODE_STATUS_FAILED
            state.error = "approval rejected"
            run.status = RUN_STATUS_FAILED
            return run

        state.status = NODE_STATUS_PENDING
        run.shared_state.pop("resume_token", None)
        run.status = RUN_STATUS_RUNNING
        return self.run(run)

    def _execute(self, executor: WorkflowNodeExecutor, node, run: WorkflowRun) -> NodeResult:
        run.shared_state["runtime_context"] = self.context
        return executor.execute(node, run, self.context)

    def _emit_node(self, run: WorkflowRun, node: Any, status: str, result: NodeResult | None = None) -> None:
        event = process_event_for_node(node, status, result, node_id=node.node_id)
        if event is not None:
            self._emit(run, event)

    def _emit(self, run: WorkflowRun, event: dict[str, Any]) -> None:
        emitted = emit_process_event(self.process_sink, event)
        run.metadata.setdefault("process_events", []).append(emitted)

    def _make_state(self, node_id: str):
        from agent_runtime_framework.workflow.state.models import NodeState

        return NodeState(node_id=node_id, status=NODE_STATUS_PENDING)
