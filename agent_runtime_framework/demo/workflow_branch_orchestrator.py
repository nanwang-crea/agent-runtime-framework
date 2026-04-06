from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_runtime_framework.workflow import WorkflowRun
from agent_runtime_framework.workflow.goal_intake import build_goal_envelope
from agent_runtime_framework.workflow.routing_runtime import RootGraphPayload, RuntimePayload


BuildRuntimeFn = Callable[[], Any]
PayloadFn = Callable[[Any], dict[str, Any]]
MemoryFn = Callable[[], dict[str, Any]]
RememberFn = Callable[[str, Any], None]


@dataclass(slots=True)
class WorkflowBranchOrchestrator:
    build_graph_execution_runtime: BuildRuntimeFn
    workflow_payload: PayloadFn
    memory_payload: MemoryFn
    remember_workflow_run: RememberFn
    application_context: Any
    workspace_root: Any
    context: Any

    def run(self, message: str, *, graph: Any, root_graph: RootGraphPayload | None = None) -> RuntimePayload:
        runtime = self.build_graph_execution_runtime()
        run = WorkflowRun(goal=message, graph=graph)
        if root_graph is not None:
            run.metadata["root_graph"] = dict(root_graph)
        run.shared_state["goal_envelope"] = build_goal_envelope(
            message,
            application_context=self.application_context,
            workspace_root=self.workspace_root,
            context=self.context,
        ).as_payload()
        run.shared_state["memory"] = self.memory_payload()
        run.shared_state["session_memory_snapshot"] = self.application_context.session_memory.snapshot()
        run = runtime.run(run)
        self.remember_workflow_run(message, run)
        return self.workflow_payload(run)
