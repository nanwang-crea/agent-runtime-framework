from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.demo.agent_branch_orchestrator import AgentBranchOrchestrator
from agent_runtime_framework.demo.compat_workflow_orchestrator import CompatWorkflowOrchestrator
from agent_runtime_framework.demo.pending_run_registry import PendingRunRegistry
from agent_runtime_framework.demo.run_lifecycle_service import RunLifecycleService
from agent_runtime_framework.demo.workflow_presentation_service import WorkflowPresentationService
from agent_runtime_framework.demo.workflow_payload_builder import WorkflowPayloadBuilder
from agent_runtime_framework.demo.workflow_run_observer import WorkflowRunObserver
from agent_runtime_framework.workflow import AgentGraphRuntime, GraphExecutionRuntime, RootGraphRuntime
from agent_runtime_framework.workflow.clarification_executor import ClarificationExecutor
from agent_runtime_framework.workflow.content_search_executor import ContentSearchExecutor
from agent_runtime_framework.workflow.discovery_executor import WorkspaceDiscoveryExecutor
from agent_runtime_framework.workflow.evidence_synthesis_executor import EvidenceSynthesisExecutor
from agent_runtime_framework.workflow.node_executors import AggregationExecutor, ApprovalGateExecutor, FinalResponseExecutor, VerificationExecutor
from agent_runtime_framework.workflow.target_resolution_executor import TargetResolutionExecutor
from agent_runtime_framework.workflow.tool_call_executor import ToolCallExecutor
from agent_runtime_framework.workflow.workspace_subtask import WorkspaceSubtaskExecutor
from agent_runtime_framework.workflow.chunked_file_read_executor import ChunkedFileReadExecutor


@dataclass(slots=True)
class DemoRuntimeFactory:
    app: Any

    def _run_conversation_branch(self, message: str, graph: Any, root_graph: dict[str, Any]) -> dict[str, Any]:
        return self.build_compat_workflow_orchestrator().run(message, graph=graph, root_graph=root_graph)

    def _run_agent_branch(self, message: str, goal: Any, root_graph: dict[str, Any]) -> dict[str, Any]:
        return self.build_agent_branch_orchestrator().run(message, goal_spec=goal, root_graph=root_graph)

    def build_graph_execution_runtime(self) -> GraphExecutionRuntime:
        return GraphExecutionRuntime(
            executors={
                "workspace_discovery": WorkspaceDiscoveryExecutor(),
                "content_search": ContentSearchExecutor(),
                "chunked_file_read": ChunkedFileReadExecutor(),
                "evidence_synthesis": EvidenceSynthesisExecutor(),
                "aggregate_results": AggregationExecutor(),
                "verification": VerificationExecutor(),
                "approval_gate": ApprovalGateExecutor(),
                "final_response": FinalResponseExecutor(),
                "tool_call": ToolCallExecutor(),
                "clarification": ClarificationExecutor(),
                "target_resolution": TargetResolutionExecutor(),
                "workspace_subtask": WorkspaceSubtaskExecutor(run_subtask=self.app._run_workspace_subtask),
            },
            context=self.app._workflow_runtime_context(),
        )

    def build_agent_graph_runtime(self) -> AgentGraphRuntime:
        return AgentGraphRuntime(
            workflow_runtime=self.build_graph_execution_runtime(),
            context=self.app._workflow_runtime_context(),
        )

    def build_observer(self) -> WorkflowRunObserver:
        return WorkflowRunObserver(context=self.app.context, workspace=self.app.workspace, task_history=self.app._task_history)

    def build_pending_run_registry(self) -> PendingRunRegistry:
        return PendingRunRegistry(
            entries=self.app._pending_tokens,
            build_agent_graph_runtime=self.build_agent_graph_runtime,
            build_graph_execution_runtime=self.build_graph_execution_runtime,
        )

    def build_workflow_presentation_service(self) -> WorkflowPresentationService:
        builder = WorkflowPayloadBuilder(
            session_payload=self.app.session_payload,
            plan_history_payload=self.app.plan_history_payload,
            run_history_payload=self.app.run_history_payload,
            memory_payload=self.app.memory_payload,
            context_payload=self.app.context_payload,
            with_router_trace=self.app._with_router_trace,
            workspace=str(self.app.workspace),
        )
        return WorkflowPresentationService(
            payload_builder=builder,
            pending_run_registry=self.build_pending_run_registry(),
            get_pending_clarification=self.app._get_pending_workflow_clarification,
            set_pending_clarification=self.app._set_pending_workflow_clarification,
        )

    def build_agent_branch_orchestrator(self) -> AgentBranchOrchestrator:
        observer = self.build_observer()
        presentation = self.build_workflow_presentation_service()
        return AgentBranchOrchestrator(
            build_agent_graph_runtime=self.build_agent_graph_runtime,
            build_runtime_context=self.app._workflow_runtime_context,
            workflow_store=self.app._workflow_store,
            workflow_payload=presentation.build_payload,
            remember_workflow_run=observer.remember_workflow_run,
            capture_workflow_codex_history=observer.capture_workflow_codex_history,
            application_context=self.app.context.application_context,
            workspace=self.app.workspace,
            context=self.app.context,
            get_pending_clarification=self.app._get_pending_workflow_clarification,
            record_run=self.app._record_run,
            run_history_payload=self.app.run_history_payload,
        )

    def build_compat_workflow_orchestrator(self) -> CompatWorkflowOrchestrator:
        observer = self.build_observer()
        presentation = self.build_workflow_presentation_service()
        return CompatWorkflowOrchestrator(
            build_graph_execution_runtime=self.build_graph_execution_runtime,
            workflow_payload=presentation.build_payload,
            memory_payload=self.app.memory_payload,
            remember_workflow_run=observer.remember_workflow_run,
            capture_workflow_codex_history=observer.capture_workflow_codex_history,
            application_context=self.app.context.application_context,
            workspace_root=self.app.workspace,
            context=self.app.context,
        )

    def build_run_lifecycle_service(self) -> RunLifecycleService:
        observer = self.build_observer()
        presentation = self.build_workflow_presentation_service()
        return RunLifecycleService(
            pending_run_registry=self.build_pending_run_registry(),
            run_inputs=self.app._run_inputs,
            workflow_payload=presentation.build_payload,
            record_run=self.app._record_run,
            remember_workflow_run=observer.remember_workflow_run,
            capture_workflow_codex_history=observer.capture_workflow_codex_history,
            load_workflow_run=self.app._workflow_store.load,
            chat=self.app.chat,
            session_payload=self.app.session_payload,
            plan_history_payload=self.app.plan_history_payload,
            run_history_payload=self.app.run_history_payload,
            memory_payload=self.app.memory_payload,
            workspace=str(self.app.workspace),
        )

    def build_root_graph_runtime(self) -> RootGraphRuntime:
        return RootGraphRuntime(
            analyze_goal_fn=self.app._analyze_workflow_goal,
            context=self.app._workflow_runtime_context(),
            mark_route_decision=self.app._mark_route_decision,
            has_pending_clarification=self.app._has_pending_clarification,
            run_conversation=self._run_conversation_branch,
            run_agent=self._run_agent_branch,
        )
