from __future__ import annotations

from agent_runtime_framework.workflow.state.models import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_PENDING,
    WorkflowNode,
    WorkflowRun,
)


class WorkflowScheduler:
    def ready_nodes(self, run: WorkflowRun) -> list[WorkflowNode]:
        ready: list[WorkflowNode] = []
        for node in run.graph.nodes:
            state = run.node_states.setdefault(node.node_id, run_default_state(node.node_id))
            if state.status != NODE_STATUS_PENDING:
                continue
            if all(
                run.node_states.setdefault(dep, run_default_state(dep)).status == NODE_STATUS_COMPLETED
                for dep in node.dependencies
            ):
                ready.append(node)
        return ready


def run_default_state(node_id: str):
    from agent_runtime_framework.workflow.state.models import NodeState

    return NodeState(node_id=node_id)
