from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.workflow.aggregator import aggregate_node_results
from agent_runtime_framework.workflow.models import (
    GoalEnvelope,
    JudgeDecision,
    NodeResult,
    NodeState,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)


@dataclass(slots=True)
class SystemNodeManager:
    def seed_system_nodes(self, run: WorkflowRun, goal_envelope: GoalEnvelope, runtime_context: Any | None) -> None:
        if "goal_intake" not in run.node_states:
            goal_result = NodeResult(
                status="completed",
                output={
                    "goal": goal_envelope.goal,
                    "intent": goal_envelope.intent,
                    "target_hints": list(goal_envelope.target_hints),
                },
            )
            run.node_states["goal_intake"] = NodeState(node_id="goal_intake", status="completed", result=goal_result)
            run.shared_state.setdefault("node_results", {})["goal_intake"] = goal_result
        if "context_assembly" not in run.node_states:
            context_result = NodeResult(
                status="completed",
                output={
                    "memory": dict((runtime_context or {}).get("memory") or {}),
                    "policy_context": dict((runtime_context or {}).get("policy_context") or {}),
                    "workspace_root": (runtime_context or {}).get("workspace_root"),
                },
            )
            run.node_states["context_assembly"] = NodeState(
                node_id="context_assembly",
                status="completed",
                result=context_result,
            )
            run.shared_state.setdefault("node_results", {})["context_assembly"] = context_result
        plan_node_id = next(
            (node.node_id for node in run.graph.nodes if node.node_type == "plan" and node.node_id not in run.node_states),
            None,
        )
        if plan_node_id:
            plan_result = NodeResult(status="completed", output={"summary": f"prepared {plan_node_id}"})
            run.node_states[plan_node_id] = NodeState(node_id=plan_node_id, status="completed", result=plan_result)
            run.shared_state.setdefault("node_results", {})[plan_node_id] = plan_result

    def materialize_iteration_system_nodes(self, run: WorkflowRun, executed: WorkflowRun, subgraph) -> tuple[NodeResult, NodeResult]:
        iteration = subgraph.iteration
        aggregate_node_id = f"aggregate_results_{iteration}"
        evidence_node_id = f"evidence_synthesis_{iteration}"
        judge_node_id = f"judge_{iteration}"
        appended_ids = {node.node_id for node in run.graph.nodes}
        last_subgraph_node_id = subgraph.nodes[-1].node_id if subgraph.nodes else f"plan_{iteration}"
        if aggregate_node_id not in appended_ids:
            run.graph.nodes.append(
                WorkflowNode(
                    node_id=aggregate_node_id,
                    node_type="aggregate_results",
                    dependencies=[node.node_id for node in subgraph.nodes],
                )
            )
            if subgraph.nodes:
                run.graph.edges.append(WorkflowEdge(source=last_subgraph_node_id, target=aggregate_node_id))
        node_results = list((executed.shared_state.get("node_results") or {}).values())
        aggregated_result = aggregate_node_results(node_results)
        run.shared_state["aggregated_result"] = aggregated_result
        run.node_states[aggregate_node_id] = NodeState(
            node_id=aggregate_node_id,
            status=aggregated_result.status,
            result=aggregated_result,
            error=aggregated_result.error,
        )
        run.shared_state.setdefault("node_results", {})[aggregate_node_id] = aggregated_result
        if evidence_node_id not in {node.node_id for node in run.graph.nodes}:
            run.graph.nodes.append(
                WorkflowNode(node_id=evidence_node_id, node_type="evidence_synthesis", dependencies=[aggregate_node_id])
            )
            run.graph.edges.append(WorkflowEdge(source=aggregate_node_id, target=evidence_node_id))
        evidence_payload = dict(getattr(aggregated_result, "output", {}) or {})
        synthesized_payload = dict(run.shared_state.get("evidence_synthesis") or {})
        if synthesized_payload:
            for key, value in synthesized_payload.items():
                if key in {"verification", "verification_events"} and evidence_payload.get(key):
                    continue
                evidence_payload[key] = value
        evidence_payload.setdefault("summary", "")
        evidence_result = NodeResult(
            status="completed",
            output=evidence_payload,
            references=list(getattr(aggregated_result, "references", []) or []),
        )
        run.node_states[evidence_node_id] = NodeState(
            node_id=evidence_node_id,
            status=evidence_result.status,
            result=evidence_result,
            error=evidence_result.error,
        )
        run.shared_state.setdefault("node_results", {})[evidence_node_id] = evidence_result
        run.shared_state["evidence_synthesis"] = evidence_payload
        if judge_node_id not in {node.node_id for node in run.graph.nodes}:
            run.graph.nodes.append(WorkflowNode(node_id=judge_node_id, node_type="judge", dependencies=[evidence_node_id]))
            run.graph.edges.append(WorkflowEdge(source=evidence_node_id, target=judge_node_id))
        return aggregated_result, evidence_result
