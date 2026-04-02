from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime_framework.workflow.approval import WorkflowResumeToken, create_resume_token
from agent_runtime_framework.workflow.models import (
    NODE_STATUS_FAILED,
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    NODE_STATUS_WAITING_APPROVAL,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_WAITING_APPROVAL,
    NodeResult,
    WorkflowRun,
)
from agent_runtime_framework.workflow.scheduler import WorkflowScheduler


@dataclass(slots=True)
class GraphExecutionRuntime:
    executors: dict[str, Any]
    scheduler: WorkflowScheduler = field(default_factory=WorkflowScheduler)
    context: dict[str, Any] = field(default_factory=dict)

    def run(self, run: WorkflowRun) -> WorkflowRun:
        run.shared_state.setdefault("node_results", {})
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
                    run.status = RUN_STATUS_WAITING_APPROVAL
                    return run

                state.status = NODE_STATUS_RUNNING
                executor = self.executors.get(node.node_type)
                if executor is None:
                    state.status = NODE_STATUS_FAILED
                    state.error = f"No executor registered for node type: {node.node_type}"
                    state.result = NodeResult(status=NODE_STATUS_FAILED, error=state.error)
                    run.shared_state["node_results"][node.node_id] = state.result
                    run.status = RUN_STATUS_FAILED
                    return run

                result = self._execute(executor, node, run)
                state.result = result
                state.error = result.error
                state.status = result.status
                run.shared_state["node_results"][node.node_id] = result
                if result.status == NODE_STATUS_FAILED:
                    run.status = RUN_STATUS_FAILED
                    return run
                if result.status == NODE_STATUS_WAITING_APPROVAL:
                    state.approval_requested = True
                    run.shared_state["resume_token"] = create_resume_token(node.node_id)
                    run.status = RUN_STATUS_WAITING_APPROVAL
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
            result = executor.resume(node, run, state.result, approved=approved, context=self.context)
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

    def _execute(self, executor: Any, node: Any, run: WorkflowRun) -> NodeResult:
        try:
            return executor.execute(node, run, self.context)
        except TypeError:
            return executor.execute(node, run)

    def _make_state(self, node_id: str):
        from agent_runtime_framework.workflow.models import NodeState

        return NodeState(node_id=node_id, status=NODE_STATUS_PENDING)
