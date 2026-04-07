from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.execution_runtime import GraphExecutionRuntime
from agent_runtime_framework.workflow.nodes import create_workflow_node_executors


def build_workflow_graph_execution_runtime(*, context: Any) -> GraphExecutionRuntime:
    return GraphExecutionRuntime(
        executors=create_workflow_node_executors(),
        context=context,
    )
