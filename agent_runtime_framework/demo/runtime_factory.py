from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.demo.agent_branch_orchestrator import AgentBranchOrchestrator
from agent_runtime_framework.demo.pending_run_registry import PendingRunRegistry
from agent_runtime_framework.demo.run_lifecycle import RunLifecycleService
from agent_runtime_framework.demo.workflow_branch_orchestrator import WorkflowBranchOrchestrator
from agent_runtime_framework.demo.workflow_payload_builder import WorkflowPayloadBuilder
from agent_runtime_framework.demo.workflow_run_observer import WorkflowRunObserver
from agent_runtime_framework.demo.payloads import with_router_trace
from agent_runtime_framework.workflow import AgentGraphRuntime, GraphExecutionRuntime, RootGraphRuntime, analyze_goal
from agent_runtime_framework.workflow.runtime_factory import build_workflow_graph_execution_runtime


@dataclass(slots=True)
class DemoRuntimeFactory:
    app: Any

    def build_graph_execution_runtime(self) -> GraphExecutionRuntime:
        return build_workflow_graph_execution_runtime(context=self.app._workflow_runtime_context())

    def build_agent_graph_runtime(self) -> AgentGraphRuntime:
        return AgentGraphRuntime(
            workflow_runtime=self.build_graph_execution_runtime(),
            context=self.app._workflow_runtime_context(),
        )

    def build_agent_branch_orchestrator(self) -> AgentBranchOrchestrator:
        observer = WorkflowRunObserver(context=self.app.context, workspace=self.app.workspace, task_history=self.app._task_history)
        payload_builder = WorkflowPayloadBuilder(
            session_payload=self.app.session_payload,
            plan_history_payload=self.app.plan_history_payload,
            run_history_payload=self.app.run_history_payload,
            memory_payload=self.app.memory_payload,
            context_payload=self.app.context_payload,
            with_router_trace=lambda steps: with_router_trace(self.app._last_route_decision, steps),
            workspace=str(self.app.workspace),
        )
        pending_run_registry = PendingRunRegistry(
            entries=self.app._pending_tokens,
            build_agent_graph_runtime=self.build_agent_graph_runtime,
            build_graph_execution_runtime=self.build_graph_execution_runtime,
        )

        def workflow_payload(run: Any) -> dict[str, Any]:
            resume_token_id = pending_run_registry.register(run)
            payload, pending_clarification = payload_builder.build(run, resume_token_id=resume_token_id)
            self.app._pending_workflow_clarification = pending_clarification
            return payload

        return AgentBranchOrchestrator(
            build_agent_graph_runtime=self.build_agent_graph_runtime,
            build_runtime_context=self.app._workflow_runtime_context,
            workflow_store=self.app._workflow_store,
            workflow_payload=workflow_payload,
            remember_workflow_run=observer.remember_workflow_run,
            application_context=self.app.context.application_context,
            workspace=self.app.workspace,
            context=self.app.context,
            get_pending_clarification=lambda: self.app._pending_workflow_clarification,
            record_run=self.app.record_run,
            run_history_payload=self.app.run_history_payload,
        )

    def build_workflow_branch_orchestrator(self) -> WorkflowBranchOrchestrator:
        observer = WorkflowRunObserver(context=self.app.context, workspace=self.app.workspace, task_history=self.app._task_history)
        payload_builder = WorkflowPayloadBuilder(
            session_payload=self.app.session_payload,
            plan_history_payload=self.app.plan_history_payload,
            run_history_payload=self.app.run_history_payload,
            memory_payload=self.app.memory_payload,
            context_payload=self.app.context_payload,
            with_router_trace=lambda steps: with_router_trace(self.app._last_route_decision, steps),
            workspace=str(self.app.workspace),
        )
        pending_run_registry = PendingRunRegistry(
            entries=self.app._pending_tokens,
            build_agent_graph_runtime=self.build_agent_graph_runtime,
            build_graph_execution_runtime=self.build_graph_execution_runtime,
        )

        def workflow_payload(run: Any) -> dict[str, Any]:
            resume_token_id = pending_run_registry.register(run)
            payload, pending_clarification = payload_builder.build(run, resume_token_id=resume_token_id)
            self.app._pending_workflow_clarification = pending_clarification
            return payload

        return WorkflowBranchOrchestrator(
            build_graph_execution_runtime=self.build_graph_execution_runtime,
            workflow_payload=workflow_payload,
            memory_payload=self.app.memory_payload,
            remember_workflow_run=observer.remember_workflow_run,
            application_context=self.app.context.application_context,
            workspace_root=self.app.workspace,
            context=self.app.context,
        )

    def build_run_lifecycle(self) -> RunLifecycleService:
        observer = WorkflowRunObserver(context=self.app.context, workspace=self.app.workspace, task_history=self.app._task_history)
        payload_builder = WorkflowPayloadBuilder(
            session_payload=self.app.session_payload,
            plan_history_payload=self.app.plan_history_payload,
            run_history_payload=self.app.run_history_payload,
            memory_payload=self.app.memory_payload,
            context_payload=self.app.context_payload,
            with_router_trace=lambda steps: with_router_trace(self.app._last_route_decision, steps),
            workspace=str(self.app.workspace),
        )
        pending_run_registry = PendingRunRegistry(
            entries=self.app._pending_tokens,
            build_agent_graph_runtime=self.build_agent_graph_runtime,
            build_graph_execution_runtime=self.build_graph_execution_runtime,
        )

        def workflow_payload(run: Any) -> dict[str, Any]:
            resume_token_id = pending_run_registry.register(run)
            payload, pending_clarification = payload_builder.build(run, resume_token_id=resume_token_id)
            self.app._pending_workflow_clarification = pending_clarification
            return payload

        return RunLifecycleService(
            pending_run_registry=pending_run_registry,
            run_inputs=self.app._run_inputs,
            workflow_payload=workflow_payload,
            record_run=self.app.record_run,
            remember_workflow_run=observer.remember_workflow_run,
            load_workflow_run=self.app._workflow_store.load,
            chat=self.app.chat,
            session_payload=self.app.session_payload,
            plan_history_payload=self.app.plan_history_payload,
            run_history_payload=self.app.run_history_payload,
            memory_payload=self.app.memory_payload,
            workspace=str(self.app.workspace),
        )

    def build_routing_runtime(self) -> RootGraphRuntime:
        def run_conversation(message: str, graph: Any, root_graph: dict[str, Any]) -> dict[str, Any]:
            return self.build_workflow_branch_orchestrator().run(message, graph=graph, root_graph=root_graph)

        def run_agent(message: str, goal: Any, root_graph: dict[str, Any]) -> dict[str, Any]:
            return self.build_agent_branch_orchestrator().run(message, goal_spec=goal, root_graph=root_graph)

        return RootGraphRuntime(
            analyze_goal_fn=lambda message, context: analyze_goal(message, context=context),
            context=self.app._workflow_runtime_context(),
            mark_route_decision=lambda route, source: setattr(self.app, "_last_route_decision", {"route": route, "source": source}),
            has_pending_clarification=lambda: self.app._pending_workflow_clarification is not None,
            run_conversation=run_conversation,
            run_agent=run_agent,
        )
