from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.runtime.execution import GraphExecutionRuntime
from agent_runtime_framework.workflow.nodes import create_workflow_node_executors


def build_workflow_graph_execution_runtime(*, context: Any, process_sink: Any | None = None) -> GraphExecutionRuntime:
    return GraphExecutionRuntime(
        executors=create_workflow_node_executors(),
        context=context,
        process_sink=process_sink,
    )
