from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.models import AgentGraphState, GoalEnvelope, PlannedNode, PlannedSubgraph, WorkflowEdge

ALLOWED_DYNAMIC_NODE_TYPES = {
    "target_resolution",
    "workspace_discovery",
    "content_search",
    "chunked_file_read",
    "workspace_subtask",
    "tool_call",
    "verification_step",
    "aggregate_results",
    "evidence_synthesis",
}

_DEFAULT_MAX_DYNAMIC_NODES = 3


def _max_dynamic_nodes(goal_envelope: GoalEnvelope, context: Any | None) -> int:
    configured = goal_envelope.constraints.get("max_dynamic_nodes", _DEFAULT_MAX_DYNAMIC_NODES)
    if context is not None:
        services = getattr(context, "services", {}) or {}
        configured = services.get("max_dynamic_nodes", configured)
    try:
        return max(1, min(int(configured), _DEFAULT_MAX_DYNAMIC_NODES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_DYNAMIC_NODES


def _candidate_nodes(goal_envelope: GoalEnvelope) -> list[PlannedNode]:
    target_hint = goal_envelope.target_hints[0] if goal_envelope.target_hints else ""
    if goal_envelope.intent in {"file_read", "workspace_read"}:
        return [
            PlannedNode(
                node_id="content_search",
                node_type="content_search",
                reason="Need to locate the requested file before reading it",
                inputs={"target_hint": target_hint, "target_path": target_hint},
                success_criteria=["find the requested file"],
            ),
            PlannedNode(
                node_id="chunked_file_read",
                node_type="chunked_file_read",
                reason="Need grounded file content as evidence",
                inputs={"target_hint": target_hint, "target_path": target_hint},
                depends_on=["content_search"],
                success_criteria=["extract relevant file content"],
            ),
            PlannedNode(
                node_id="evidence_synthesis",
                node_type="evidence_synthesis",
                reason="Need to synthesize collected evidence for judging",
                depends_on=["chunked_file_read"],
                success_criteria=["produce a concise evidence summary"],
            ),
        ]
    if goal_envelope.intent in {"repository_overview", "workspace_discovery"}:
        return [
            PlannedNode(
                node_id="workspace_discovery",
                node_type="workspace_discovery",
                reason="Need workspace structure before answering overview questions",
                success_criteria=["collect top-level workspace structure"],
            ),
            PlannedNode(
                node_id="evidence_synthesis",
                node_type="evidence_synthesis",
                reason="Need to synthesize workspace findings for judging",
                depends_on=["workspace_discovery"],
                success_criteria=["summarize workspace evidence"],
            ),
        ]
    if goal_envelope.intent == "target_explainer":
        return [
            PlannedNode(
                node_id="target_resolution",
                node_type="target_resolution",
                reason="Need to resolve the referenced target before reading it",
                inputs={"query": goal_envelope.goal, "target_hint": target_hint},
                success_criteria=["resolve the target path or request clarification"],
            ),
            PlannedNode(
                node_id="content_search",
                node_type="content_search",
                reason="Need supporting evidence around the resolved target",
                inputs={"target_hint": target_hint, "target_path": target_hint},
                depends_on=["target_resolution"],
                success_criteria=["find relevant target evidence"],
            ),
            PlannedNode(
                node_id="chunked_file_read",
                node_type="chunked_file_read",
                reason="Need grounded file content for explanation",
                inputs={"target_path": target_hint, "target_hint": target_hint},
                depends_on=["content_search"],
                success_criteria=["read the resolved target"],
            ),
        ]
    if goal_envelope.intent in {"compound", "compound_read"}:
        return [
            PlannedNode(
                node_id="workspace_discovery",
                node_type="workspace_discovery",
                reason="Need workspace context for the compound request",
                success_criteria=["collect relevant workspace structure"],
            ),
            PlannedNode(
                node_id="content_search",
                node_type="content_search",
                reason="Need to locate the target file for the compound request",
                inputs={"target_hint": target_hint, "target_path": target_hint},
                depends_on=["workspace_discovery"],
                success_criteria=["identify the requested file"],
            ),
            PlannedNode(
                node_id="chunked_file_read",
                node_type="chunked_file_read",
                reason="Need file evidence to complement workspace context",
                inputs={"target_hint": target_hint, "target_path": target_hint},
                depends_on=["content_search"],
                success_criteria=["read the requested file content"],
            ),
        ]
    return [
        PlannedNode(
            node_id="workspace_subtask",
            node_type="workspace_subtask",
            reason="Need a flexible execution step for this goal",
            inputs={"goal": goal_envelope.goal, "intent": goal_envelope.intent},
            success_criteria=["produce progress toward the goal"],
        ),
        PlannedNode(
            node_id="evidence_synthesis",
            node_type="evidence_synthesis",
            reason="Need to summarize flexible execution outputs for judging",
            depends_on=["workspace_subtask"],
            success_criteria=["summarize subtask evidence"],
        ),
    ]


def plan_next_subgraph(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, context: Any | None) -> PlannedSubgraph:
    max_dynamic_nodes = _max_dynamic_nodes(goal_envelope, context)
    iteration = graph_state.current_iteration + 1
    base_nodes = _candidate_nodes(goal_envelope)[:max_dynamic_nodes]
    node_id_map = {node.node_id: f"{node.node_id}_{iteration}" for node in base_nodes}
    nodes = [
        PlannedNode(
            node_id=node_id_map[node.node_id],
            node_type=node.node_type,
            reason=node.reason,
            inputs=dict(node.inputs),
            depends_on=[node_id_map.get(dep, dep) for dep in node.depends_on],
            success_criteria=list(node.success_criteria),
        )
        for node in base_nodes
    ]
    edges: list[WorkflowEdge] = []
    for node in nodes:
        if node.node_type not in ALLOWED_DYNAMIC_NODE_TYPES:
            raise ValueError(f"unsupported planned node type: {node.node_type}")
        for dependency in node.depends_on:
            edges.append(WorkflowEdge(source=dependency, target=node.node_id))
    return PlannedSubgraph(
        iteration=iteration,
        planner_summary=f"Plan iteration {iteration} for {goal_envelope.intent}",
        nodes=nodes,
        edges=edges,
        metadata={"planner": "deterministic_v2", "max_dynamic_nodes": max_dynamic_nodes},
    )
