from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable
from uuid import uuid4

from agent_runtime_framework.workflow.agent_graph_state_store import AgentGraphStateStore
from agent_runtime_framework.workflow.graph_mutation import append_subgraph
from agent_runtime_framework.workflow.models import (
    AgentGraphState,
    GoalEnvelope,
    JudgeDecision,
    NodeResult,
    NodeState,
    PlannedSubgraph,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_WAITING_APPROVAL,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    new_agent_graph_state,
    normalize_aggregated_workflow_payload,
    serialize_agent_graph_state,
)
from agent_runtime_framework.workflow.execution_runtime import GraphExecutionRuntime
from agent_runtime_framework.workflow.judge import judge_progress
from agent_runtime_framework.workflow.subgraph_planner import plan_next_subgraph
from agent_runtime_framework.workflow.system_node_manager import SystemNodeManager


JudgeFn = Callable[[GoalEnvelope, dict[str, Any], AgentGraphState], JudgeDecision | dict[str, Any]]
PlannerFn = Callable[[GoalEnvelope, AgentGraphState, Any | None], PlannedSubgraph]


@dataclass(slots=True)
class AgentGraphRuntime:
    workflow_runtime: GraphExecutionRuntime
    planner: PlannerFn = plan_next_subgraph
    judge: JudgeFn | None = None
    context: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 3
    state_store: AgentGraphStateStore = field(default_factory=AgentGraphStateStore)
    system_node_manager: SystemNodeManager = field(default_factory=SystemNodeManager)

    def run(self, goal_envelope: GoalEnvelope, *, run_id: str | None = None, context: Any | None = None, prior_state: dict[str, Any] | None = None, prior_graph: WorkflowGraph | None = None, clarification_response: str | None = None) -> WorkflowRun:
        runtime_context = context if context is not None else self.context
        state = self._restore_state(goal_envelope, run_id=run_id, prior_state=prior_state)
        graph = prior_graph or self._initial_graph(state)
        run = WorkflowRun(goal=goal_envelope.goal, run_id=state.run_id, graph=graph)
        run.metadata["goal_envelope"] = goal_envelope.as_payload()
        self._seed_system_nodes(run, goal_envelope, runtime_context)
        if clarification_response:
            run.shared_state["clarification_response"] = clarification_response
            state.aggregated_payload["open_questions"] = []
            state.aggregated_payload["artifacts"] = dict(state.aggregated_payload.get("artifacts") or {})
            state.aggregated_payload["artifacts"]["clarification_response"] = [clarification_response]
        return self._execute_iterations(goal_envelope, state, run, runtime_context)

    def resume(self, run: WorkflowRun, *, resume_token: Any, approved: bool, context: Any | None = None) -> WorkflowRun:
        runtime_context = context if context is not None else self.context
        goal_envelope = GoalEnvelope(**dict(run.metadata.get("goal_envelope") or {}))
        state = self._restore_state(goal_envelope, run_id=run.run_id, prior_state=dict(run.metadata.get("agent_graph_state") or {}))
        pending_subrun_payload = dict(run.metadata.get("pending_subrun") or {})
        pending_subgraph_payload = dict(run.metadata.get("pending_subgraph") or {})
        if not pending_subrun_payload or not pending_subgraph_payload:
            run.status = RUN_STATUS_FAILED
            run.error = "missing pending agent graph approval state"
            return run
        subrun = self._restore_workflow_run(pending_subrun_payload)
        subgraph = PlannedSubgraph(
            iteration=int(pending_subgraph_payload.get("iteration") or 0),
            planner_summary=str(pending_subgraph_payload.get("planner_summary") or ""),
            nodes=[__import__("agent_runtime_framework.workflow.models", fromlist=["PlannedNode"]).PlannedNode(**node) for node in pending_subgraph_payload.get("nodes", [])],
            edges=[WorkflowEdge(**edge) for edge in pending_subgraph_payload.get("edges", [])],
            metadata=dict(pending_subgraph_payload.get("metadata") or {}),
        )
        resumed = self.workflow_runtime.resume(subrun, resume_token=resume_token, approved=approved)
        run.metadata.pop("pending_subrun", None)
        run.metadata.pop("pending_subgraph", None)
        outcome = self._consume_subrun(goal_envelope, state, run, resumed, subgraph)
        if outcome is not None:
            return outcome
        return self._execute_iterations(goal_envelope, state, run, runtime_context)

    def _execute_iterations(self, goal_envelope: GoalEnvelope, state: AgentGraphState, run: WorkflowRun, runtime_context: Any | None) -> WorkflowRun:
        graph = run.graph
        while state.current_iteration < self.max_iterations:
            subgraph = self.planner(goal_envelope, state, runtime_context)
            anchor_node_id = self._anchor_node_id(graph, state)
            graph = append_subgraph(graph, subgraph, after_node_id=anchor_node_id)
            run.graph = graph
            subrun = WorkflowRun(goal=goal_envelope.goal, graph=self._execution_graph(subgraph))
            subrun.shared_state["node_results"] = dict(run.shared_state.get("node_results") or {})
            executed = self.workflow_runtime.run(subrun)
            outcome = self._consume_subrun(goal_envelope, state, run, executed, subgraph)
            if outcome is not None:
                return outcome
            graph = run.graph
        last_decision = state.judge_history[-1] if state.judge_history else JudgeDecision(status="accepted", reason="completed")
        run.status = RUN_STATUS_COMPLETED
        run.final_output = self._limited_answer(last_decision)
        run.metadata["agent_graph_state"] = serialize_agent_graph_state(state)
        run.metadata["append_history"] = list(run.graph.metadata.get("append_history") or [])
        return run

    def _consume_subrun(self, goal_envelope: GoalEnvelope, state: AgentGraphState, run: WorkflowRun, executed: WorkflowRun, subgraph: PlannedSubgraph) -> WorkflowRun | None:
        run.node_states.update(dict(executed.node_states or {}))
        run.shared_state.setdefault("node_results", {}).update(dict(executed.shared_state.get("node_results") or {}))
        for key in ("evidence_synthesis", "clarification_request", "resolved_target"):
            if key in executed.shared_state:
                run.shared_state[key] = executed.shared_state[key]
        if executed.status == RUN_STATUS_FAILED:
            run.status = RUN_STATUS_FAILED
            run.error = executed.error
            run.metadata["agent_graph_state"] = serialize_agent_graph_state(state)
            run.metadata["append_history"] = list(run.graph.metadata.get("append_history") or [])
            return run
        if executed.status == RUN_STATUS_WAITING_APPROVAL:
            run.status = RUN_STATUS_WAITING_APPROVAL
            if "resume_token" in executed.shared_state:
                run.shared_state["resume_token"] = executed.shared_state["resume_token"]
            run.metadata["pending_subrun"] = asdict(executed)
            run.metadata["pending_subgraph"] = subgraph.as_payload()
            run.metadata["agent_graph_state"] = serialize_agent_graph_state(state)
            run.metadata["append_history"] = list(run.graph.metadata.get("append_history") or [])
            return run
        if "evidence_synthesis" in executed.shared_state:
            run.shared_state["evidence_synthesis"] = executed.shared_state["evidence_synthesis"]
        state.current_iteration = subgraph.iteration
        state.planned_subgraphs.append(subgraph)
        state.appended_node_ids.extend(node.node_id for node in subgraph.nodes)
        planner_summary = str(subgraph.planner_summary or "").strip()
        if planner_summary and planner_summary not in state.attempted_strategies:
            state.attempted_strategies.append(planner_summary)
        aggregated_result, evidence_result = self._materialize_iteration_system_nodes(
            run,
            executed,
            subgraph,
            JudgeDecision(status="accepted", reason="pending"),
        )
        state.aggregated_payload = normalize_aggregated_workflow_payload(getattr(evidence_result, "output", {}) or getattr(aggregated_result, "output", {}) or {})

        if run.shared_state.get("clarification_request"):
            request = dict(run.shared_state.get("clarification_request") or {})
            last_decision = JudgeDecision(
                status="needs_clarification",
                reason=str(request.get("prompt") or request.get("summary") or "Please clarify the request."),
                missing_evidence=[str(request.get("prompt") or "")],
            )
        else:
            last_decision = self._judge(goal_envelope, state)
        state.judge_history.append(last_decision)
        state.open_issues = list(last_decision.missing_evidence)
        state.iteration_summaries.append(
            {
                "iteration": state.current_iteration,
                "planner_summary": subgraph.planner_summary,
                "node_ids": [node.node_id for node in subgraph.nodes],
                "judge_status": last_decision.status,
                "judge_reason": last_decision.reason,
                "missing_evidence": list(last_decision.missing_evidence),
                "diagnosis": dict(last_decision.diagnosis),
                "strategy_guidance": dict(last_decision.strategy_guidance),
            }
        )
        if last_decision.status != "accepted":
            state.failure_history.append(
                {
                    "iteration": state.current_iteration,
                    "status": last_decision.status,
                    "reason": last_decision.reason,
                    "missing_evidence": list(last_decision.missing_evidence),
                    "diagnosis": dict(last_decision.diagnosis),
                    "strategy_guidance": dict(last_decision.strategy_guidance),
                }
            )
        state.execution_summary = {
            "current_iteration": state.current_iteration,
            "last_judge_status": last_decision.status,
            "last_judge_reason": last_decision.reason,
            "missing_evidence": list(last_decision.missing_evidence),
            "appended_node_ids": list(state.appended_node_ids),
            "summaries": list(state.aggregated_payload.get("summaries", []) or []),
            "verification": dict(state.aggregated_payload.get("verification") or {}) if isinstance(state.aggregated_payload.get("verification"), dict) else None,
            "open_issues": list(state.open_issues),
            "attempted_strategies": list(state.attempted_strategies),
            "latest_diagnosis": dict(last_decision.diagnosis),
            "latest_strategy_guidance": dict(last_decision.strategy_guidance),
            "latest_failure": dict(state.failure_history[-1]) if state.failure_history else None,
        }
        run.shared_state["judge_decision"] = last_decision.as_payload()
        judge_node_id = f"judge_{state.current_iteration}"
        judge_result = NodeResult(status="completed", output=last_decision.as_payload())
        run.node_states[judge_node_id] = NodeState(node_id=judge_node_id, status=judge_result.status, result=judge_result, error=judge_result.error)
        run.shared_state.setdefault("node_results", {})[judge_node_id] = judge_result

        if last_decision.status == "accepted":
            run.status = RUN_STATUS_COMPLETED
            run.final_output = self._finalize(run, last_decision)
            run.metadata["agent_graph_state"] = serialize_agent_graph_state(state)
            run.metadata["append_history"] = list(run.graph.metadata.get("append_history") or [])
            return run
        if last_decision.status == "stop_due_to_cost":
            run.status = RUN_STATUS_COMPLETED
            run.final_output = self._finalize(run, last_decision, limited=True)
            run.metadata["agent_graph_state"] = serialize_agent_graph_state(state)
            run.metadata["append_history"] = list(run.graph.metadata.get("append_history") or [])
            return run
        if last_decision.status == "needs_clarification":
            prompt = last_decision.reason or "Please clarify the request."
            clarification_node = WorkflowNode(node_id=f"clarification_{state.current_iteration}", node_type="clarification", metadata={"prompt": prompt})
            run.graph.nodes.append(clarification_node)
            run.graph.edges.append(WorkflowEdge(source=f"judge_{state.current_iteration}", target=clarification_node.node_id))
            if "clarification" in self.workflow_runtime.executors:
                self._execute_system_graph(run, [WorkflowNode(node_id=clarification_node.node_id, node_type="clarification", metadata={"prompt": prompt})])
            else:
                run.shared_state["clarification_request"] = {"prompt": prompt, "summary": prompt, "clarification_required": True}
            run.status = RUN_STATUS_COMPLETED
            run.final_output = prompt
            run.metadata["agent_graph_state"] = serialize_agent_graph_state(state)
            run.metadata["append_history"] = list(run.graph.metadata.get("append_history") or [])
            return run
        next_plan_node_id = f"plan_{state.current_iteration + 1}"
        if next_plan_node_id not in {node.node_id for node in run.graph.nodes} and state.current_iteration < self.max_iterations:
            run.graph.nodes.append(WorkflowNode(node_id=next_plan_node_id, node_type="plan", dependencies=[f"judge_{state.current_iteration}"]))
            run.graph.edges.append(WorkflowEdge(source=f"judge_{state.current_iteration}", target=next_plan_node_id))
            plan_result = NodeResult(status="completed", output={"summary": f"prepared {next_plan_node_id}"})
            run.node_states[next_plan_node_id] = NodeState(node_id=next_plan_node_id, status="completed", result=plan_result)
            run.shared_state.setdefault("node_results", {})[next_plan_node_id] = plan_result
        run.metadata["agent_graph_state"] = serialize_agent_graph_state(state)
        run.metadata["append_history"] = list(run.graph.metadata.get("append_history") or [])
        return None

    def _restore_workflow_run(self, payload: dict[str, Any]) -> WorkflowRun:
        return self.state_store.restore_workflow_run(payload)

    def _initial_graph(self, state: AgentGraphState) -> WorkflowGraph:
        next_iteration = state.current_iteration + 1
        return WorkflowGraph(
            nodes=[
                WorkflowNode(node_id="goal_intake", node_type="goal_intake"),
                WorkflowNode(node_id="context_assembly", node_type="context_assembly", dependencies=["goal_intake"]),
                WorkflowNode(node_id=f"plan_{next_iteration}", node_type="plan", dependencies=["context_assembly"]),
            ],
            edges=[
                WorkflowEdge(source="goal_intake", target="context_assembly"),
                WorkflowEdge(source="context_assembly", target=f"plan_{next_iteration}"),
            ],
            metadata={"agent_graph": True},
        )

    def _seed_system_nodes(self, run: WorkflowRun, goal_envelope: GoalEnvelope, runtime_context: Any | None) -> None:
        self.system_node_manager.seed_system_nodes(run, goal_envelope, runtime_context)

    def _materialize_iteration_system_nodes(self, run: WorkflowRun, executed: WorkflowRun, subgraph: PlannedSubgraph, last_decision: JudgeDecision) -> tuple[NodeResult, NodeResult]:
        return self.system_node_manager.materialize_iteration_system_nodes(run, executed, subgraph)

    def _judge(self, goal_envelope: GoalEnvelope, state: AgentGraphState) -> JudgeDecision:
        if self.judge is None:
            return judge_progress(goal_envelope, state.aggregated_payload, state)
        decision = self.judge(goal_envelope, state.aggregated_payload, state)
        if isinstance(decision, JudgeDecision):
            return decision
        return JudgeDecision(
            status=str(decision.get("status") or "accepted"),
            reason=str(decision.get("reason") or "completed"),
            missing_evidence=[str(item) for item in decision.get("missing_evidence", []) or []],
            coverage_report=dict(decision.get("coverage_report") or {}),
            replan_hint=dict(decision.get("replan_hint") or {}),
            diagnosis=dict(decision.get("diagnosis") or {}),
            strategy_guidance=dict(decision.get("strategy_guidance") or {}),
        )

    def _execution_graph(self, subgraph: PlannedSubgraph) -> WorkflowGraph:
        return WorkflowGraph(
            nodes=[
                WorkflowNode(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    dependencies=list(node.depends_on),
                    requires_approval=node.requires_approval,
                    metadata=dict(node.inputs or {}),
                )
                for node in subgraph.nodes
            ],
            edges=list(subgraph.edges),
        )

    def _anchor_node_id(self, graph: WorkflowGraph, state: AgentGraphState) -> str:
        if state.current_iteration <= 0:
            return "plan_1"
        return f"judge_{state.current_iteration}"

    def _limited_answer(self, decision: JudgeDecision) -> str:
        missing = ""
        if decision.missing_evidence:
            missing = f" Missing: {', '.join(decision.missing_evidence)}."
        return f"Iteration limit reached. Reason: {decision.reason}.{missing}".strip()

    def _restore_state(self, goal_envelope: GoalEnvelope, *, run_id: str | None, prior_state: dict[str, Any] | None) -> AgentGraphState:
        return self.state_store.restore_state(goal_envelope, run_id=run_id, prior_state=prior_state)

    def _finalize(self, run: WorkflowRun, decision: JudgeDecision, limited: bool = False) -> str:
        run.shared_state["judge_decision"] = decision.as_payload()
        system_nodes: list[WorkflowNode] = []
        if "evidence_synthesis" in self.workflow_runtime.executors and "evidence_synthesis" not in run.shared_state:
            synthesis_node_id = "evidence_synthesis"
            if not any(node.node_id == synthesis_node_id for node in run.graph.nodes):
                run.graph.nodes.append(WorkflowNode(node_id=synthesis_node_id, node_type="evidence_synthesis"))
            system_nodes.append(WorkflowNode(node_id=synthesis_node_id, node_type="evidence_synthesis"))
        final_executor = self.workflow_runtime.executors.get("final_response")
        if final_executor is None:
            return self._limited_answer(decision) if limited else decision.reason
        final_node_id = "final_response"
        if not any(node.node_id == final_node_id for node in run.graph.nodes):
            run.graph.nodes.append(WorkflowNode(node_id=final_node_id, node_type="final_response"))
        final_dependencies = [system_nodes[-1].node_id] if system_nodes else []
        system_nodes.append(WorkflowNode(node_id=final_node_id, node_type="final_response", dependencies=final_dependencies))
        executed = self._execute_system_graph(run, system_nodes)
        result = executed.node_states[final_node_id].result if final_node_id in executed.node_states else None
        if result is None:
            return self._limited_answer(decision) if limited else decision.reason
        if result.status == "completed" and isinstance(result.output, dict):
            return str(result.output.get("final_response") or run.final_output or decision.reason)
        return self._limited_answer(decision) if limited else decision.reason

    def _execute_system_graph(self, run: WorkflowRun, nodes: list[WorkflowNode]) -> WorkflowRun:
        edges: list[WorkflowEdge] = []
        for previous, current in zip(nodes, nodes[1:]):
            if current.node_id in set(current.dependencies):
                continue
            edges.append(WorkflowEdge(source=previous.node_id, target=current.node_id))
        system_run = WorkflowRun(
            goal=run.goal,
            graph=WorkflowGraph(nodes=nodes, edges=edges),
            shared_state=run.shared_state,
        )
        executed = self.workflow_runtime.run(system_run)
        run.node_states.update(executed.node_states)
        run.shared_state.setdefault("node_results", {}).update(dict(executed.shared_state.get("node_results") or {}))
        if executed.final_output:
            run.final_output = executed.final_output
        return executed
