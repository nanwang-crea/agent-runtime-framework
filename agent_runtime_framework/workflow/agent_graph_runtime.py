from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable
from uuid import uuid4

from agent_runtime_framework.workflow.aggregator import aggregate_node_results
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
from agent_runtime_framework.workflow.planner_v2 import plan_next_subgraph
from agent_runtime_framework.workflow.runtime import WorkflowRuntime
from agent_runtime_framework.workflow.judge import judge_progress


JudgeFn = Callable[[GoalEnvelope, dict[str, Any], AgentGraphState], JudgeDecision | dict[str, Any]]
PlannerFn = Callable[[GoalEnvelope, AgentGraphState, Any | None], PlannedSubgraph]


@dataclass(slots=True)
class AgentGraphRuntime:
    workflow_runtime: WorkflowRuntime
    planner: PlannerFn = plan_next_subgraph
    judge: JudgeFn | None = None
    context: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 3

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
        for key in ("evidence_synthesis", "clarification_request", "workspace_subtask_results", "resolved_target"):
            if key in executed.shared_state:
                run.shared_state[key] = executed.shared_state[key]
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
        aggregated_result, evidence_result = self._materialize_iteration_system_nodes(run, executed, subgraph, JudgeDecision(status="accepted", reason="pending"))
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
            clarification_executor = self.workflow_runtime.executors.get("clarification")
            if clarification_executor is not None:
                result = self.workflow_runtime._execute(clarification_executor, clarification_node, run)
                run.shared_state.setdefault("node_results", {})[clarification_node.node_id] = result
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
        graph_payload = payload.get("graph", {})
        graph = WorkflowGraph(
            nodes=[WorkflowNode(**item) for item in graph_payload.get("nodes", [])],
            edges=[WorkflowEdge(**item) for item in graph_payload.get("edges", [])],
            metadata=dict(graph_payload.get("metadata", {})),
        )
        shared_state = dict(payload.get("shared_state", {}))
        resume_token_payload = shared_state.get("resume_token")
        if isinstance(resume_token_payload, dict):
            from agent_runtime_framework.workflow.approval import WorkflowResumeToken

            shared_state["resume_token"] = WorkflowResumeToken(
                token_id=str(resume_token_payload.get("token_id") or ""),
                node_id=str(resume_token_payload.get("node_id") or ""),
            )
        run = WorkflowRun(
            run_id=str(payload.get("run_id") or ""),
            goal=str(payload.get("goal") or ""),
            graph=graph,
            shared_state=shared_state,
            status=str(payload.get("status") or "pending"),
            final_output=payload.get("final_output"),
            error=payload.get("error"),
            metadata=dict(payload.get("metadata", {})),
        )
        for node_id, state_payload in dict(payload.get("node_states", {})).items():
            result_payload = state_payload.get("result")
            result = NodeResult(**result_payload) if isinstance(result_payload, dict) else None
            run.node_states[node_id] = NodeState(
                node_id=str(state_payload.get("node_id") or node_id),
                status=str(state_payload.get("status") or "pending"),
                result=result,
                error=state_payload.get("error"),
                approval_requested=bool(state_payload.get("approval_requested", False)),
                approval_granted=state_payload.get("approval_granted"),
                attempts=int(state_payload.get("attempts", 0)),
                metadata=dict(state_payload.get("metadata", {})),
            )
        return run

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
        if "goal_intake" not in run.node_states:
            goal_result = NodeResult(status="completed", output={"goal": goal_envelope.goal, "intent": goal_envelope.intent, "target_hints": list(goal_envelope.target_hints)})
            run.node_states["goal_intake"] = NodeState(node_id="goal_intake", status="completed", result=goal_result)
            run.shared_state.setdefault("node_results", {})["goal_intake"] = goal_result
        if "context_assembly" not in run.node_states:
            context_result = NodeResult(status="completed", output={"memory": dict((runtime_context or {}).get("memory") or {}), "policy_context": dict((runtime_context or {}).get("policy_context") or {}), "workspace_root": (runtime_context or {}).get("workspace_root")})
            run.node_states["context_assembly"] = NodeState(node_id="context_assembly", status="completed", result=context_result)
            run.shared_state.setdefault("node_results", {})["context_assembly"] = context_result
        plan_node_id = next((node.node_id for node in run.graph.nodes if node.node_type == "plan" and node.node_id not in run.node_states), None)
        if plan_node_id:
            plan_result = NodeResult(status="completed", output={"summary": f"prepared {plan_node_id}"})
            run.node_states[plan_node_id] = NodeState(node_id=plan_node_id, status="completed", result=plan_result)
            run.shared_state.setdefault("node_results", {})[plan_node_id] = plan_result

    def _materialize_iteration_system_nodes(self, run: WorkflowRun, executed: WorkflowRun, subgraph: PlannedSubgraph, last_decision: JudgeDecision) -> tuple[NodeResult, NodeResult]:
        iteration = subgraph.iteration
        aggregate_node_id = f"aggregate_results_{iteration}"
        evidence_node_id = f"evidence_synthesis_{iteration}"
        judge_node_id = f"judge_{iteration}"
        appended_ids = {node.node_id for node in run.graph.nodes}
        last_subgraph_node_id = subgraph.nodes[-1].node_id if subgraph.nodes else f"plan_{iteration}"
        if aggregate_node_id not in appended_ids:
            run.graph.nodes.append(WorkflowNode(node_id=aggregate_node_id, node_type="aggregate_results", dependencies=[node.node_id for node in subgraph.nodes]))
            if subgraph.nodes:
                run.graph.edges.append(WorkflowEdge(source=last_subgraph_node_id, target=aggregate_node_id))
        node_results = list((executed.shared_state.get("node_results") or {}).values())
        aggregated_result = aggregate_node_results(node_results)
        run.shared_state["aggregated_result"] = aggregated_result
        run.node_states[aggregate_node_id] = NodeState(node_id=aggregate_node_id, status=aggregated_result.status, result=aggregated_result, error=aggregated_result.error)
        run.shared_state.setdefault("node_results", {})[aggregate_node_id] = aggregated_result
        if evidence_node_id not in {node.node_id for node in run.graph.nodes}:
            run.graph.nodes.append(WorkflowNode(node_id=evidence_node_id, node_type="evidence_synthesis", dependencies=[aggregate_node_id]))
            run.graph.edges.append(WorkflowEdge(source=aggregate_node_id, target=evidence_node_id))
        evidence_payload = dict(getattr(aggregated_result, "output", {}) or {})
        synthesized_payload = dict(run.shared_state.get("evidence_synthesis") or {})
        if synthesized_payload:
            evidence_payload.update(synthesized_payload)
        evidence_payload.setdefault("summary", "")
        evidence_result = NodeResult(status="completed", output=evidence_payload, references=list(getattr(aggregated_result, "references", []) or []))
        run.node_states[evidence_node_id] = NodeState(node_id=evidence_node_id, status=evidence_result.status, result=evidence_result, error=evidence_result.error)
        run.shared_state.setdefault("node_results", {})[evidence_node_id] = evidence_result
        run.shared_state["evidence_synthesis"] = evidence_payload
        if judge_node_id not in {node.node_id for node in run.graph.nodes}:
            run.graph.nodes.append(WorkflowNode(node_id=judge_node_id, node_type="judge", dependencies=[evidence_node_id]))
            run.graph.edges.append(WorkflowEdge(source=evidence_node_id, target=judge_node_id))
        return aggregated_result, evidence_result

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
        )

    def _execution_graph(self, subgraph: PlannedSubgraph) -> WorkflowGraph:
        return WorkflowGraph(
            nodes=[
                WorkflowNode(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    dependencies=list(node.depends_on),
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
        if not prior_state:
            return new_agent_graph_state(run_id=run_id or str(uuid4()), goal_envelope=goal_envelope)
        state = AgentGraphState(
            run_id=str(prior_state.get("run_id") or run_id or str(uuid4())),
            goal_envelope=goal_envelope,
            current_iteration=int(prior_state.get("current_iteration") or 0),
            aggregated_payload=normalize_aggregated_workflow_payload(prior_state.get("aggregated_payload") or {}),
            planned_subgraphs=[],
            judge_history=[],
            appended_node_ids=[str(item) for item in prior_state.get("appended_node_ids", []) or []],
        )
        for item in prior_state.get("planned_subgraphs", []) or []:
            nodes=[__import__("agent_runtime_framework.workflow.models", fromlist=["PlannedNode"]).PlannedNode(**node) for node in item.get("nodes", [])]
            edges=[WorkflowEdge(**edge) for edge in item.get("edges", [])]
            state.planned_subgraphs.append(PlannedSubgraph(iteration=int(item.get("iteration") or 0), planner_summary=str(item.get("planner_summary") or ""), nodes=nodes, edges=edges, metadata=dict(item.get("metadata") or {})))
        for item in prior_state.get("judge_history", []) or []:
            state.judge_history.append(JudgeDecision(status=str(item.get("status") or "accepted"), reason=str(item.get("reason") or ""), missing_evidence=[str(v) for v in item.get("missing_evidence", []) or []], coverage_report=dict(item.get("coverage_report") or {}), replan_hint=dict(item.get("replan_hint") or {})))
        return state

    def _finalize(self, run: WorkflowRun, decision: JudgeDecision, limited: bool = False) -> str:
        run.shared_state["judge_decision"] = decision.as_payload()
        synthesis_executor = self.workflow_runtime.executors.get("evidence_synthesis")
        if synthesis_executor is not None and "evidence_synthesis" not in run.shared_state:
            synthesis_node_id = "evidence_synthesis"
            if not any(node.node_id == synthesis_node_id for node in run.graph.nodes):
                run.graph.nodes.append(WorkflowNode(node_id=synthesis_node_id, node_type="evidence_synthesis"))
            synthesis_result = self.workflow_runtime._execute(synthesis_executor, WorkflowNode(node_id=synthesis_node_id, node_type="evidence_synthesis"), run)
            run.node_states[synthesis_node_id] = NodeState(node_id=synthesis_node_id, status=synthesis_result.status, result=synthesis_result, error=synthesis_result.error)
            run.shared_state.setdefault("node_results", {})[synthesis_node_id] = synthesis_result
        final_executor = self.workflow_runtime.executors.get("final_response")
        if final_executor is None:
            return self._limited_answer(decision) if limited else decision.reason
        final_node_id = "final_response"
        if not any(node.node_id == final_node_id for node in run.graph.nodes):
            run.graph.nodes.append(WorkflowNode(node_id=final_node_id, node_type="final_response"))
        result = self.workflow_runtime._execute(final_executor, WorkflowNode(node_id=final_node_id, node_type="final_response"), run)
        run.node_states[final_node_id] = NodeState(node_id=final_node_id, status=result.status, result=result, error=result.error)
        run.shared_state.setdefault("node_results", {})[final_node_id] = result
        if result.status == "completed" and isinstance(result.output, dict):
            return str(result.output.get("final_response") or run.final_output or decision.reason)
        return self._limited_answer(decision) if limited else decision.reason
