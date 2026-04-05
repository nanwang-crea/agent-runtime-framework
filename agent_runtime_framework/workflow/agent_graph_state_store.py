from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent_runtime_framework.workflow.models import (
    AgentGraphState,
    GoalEnvelope,
    JudgeDecision,
    NodeResult,
    NodeState,
    PlannedSubgraph,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    new_agent_graph_state,
    normalize_aggregated_workflow_payload,
)


@dataclass(slots=True)
class AgentGraphStateStore:
    def restore_state(
        self,
        goal_envelope: GoalEnvelope,
        *,
        run_id: str | None,
        prior_state: dict[str, Any] | None,
    ) -> AgentGraphState:
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
            nodes = [
                __import__("agent_runtime_framework.workflow.models", fromlist=["PlannedNode"]).PlannedNode(**node)
                for node in item.get("nodes", [])
            ]
            edges = [WorkflowEdge(**edge) for edge in item.get("edges", [])]
            state.planned_subgraphs.append(
                PlannedSubgraph(
                    iteration=int(item.get("iteration") or 0),
                    planner_summary=str(item.get("planner_summary") or ""),
                    nodes=nodes,
                    edges=edges,
                    metadata=dict(item.get("metadata") or {}),
                )
            )
        for item in prior_state.get("judge_history", []) or []:
            state.judge_history.append(
                JudgeDecision(
                    status=str(item.get("status") or "accepted"),
                    reason=str(item.get("reason") or ""),
                    missing_evidence=[str(v) for v in item.get("missing_evidence", []) or []],
                    coverage_report=dict(item.get("coverage_report") or {}),
                    replan_hint=dict(item.get("replan_hint") or {}),
                )
            )
        return state

    def restore_workflow_run(self, payload: dict[str, Any]) -> WorkflowRun:
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
