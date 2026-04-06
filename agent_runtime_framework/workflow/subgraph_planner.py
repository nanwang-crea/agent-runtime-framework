from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.llm_access import get_application_context
from agent_runtime_framework.workflow.models import AgentGraphState, GoalEnvelope, PlannedNode, PlannedSubgraph, WorkflowEdge
from agent_runtime_framework.workflow.planner_prompts import build_subgraph_planner_system_prompt
from agent_runtime_framework.workflow.prompting import extract_json_block

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
_DEFAULT_PLANNER_MODE = "model_with_fallback"
def _max_dynamic_nodes(goal_envelope: GoalEnvelope, context: Any | None) -> int:
    configured = goal_envelope.constraints.get("max_dynamic_nodes", _DEFAULT_MAX_DYNAMIC_NODES)
    if context is not None:
        services = getattr(context, "services", {}) or {}
        configured = services.get("max_dynamic_nodes", configured)
    try:
        return max(1, min(int(configured), _DEFAULT_MAX_DYNAMIC_NODES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_DYNAMIC_NODES


def _planner_mode(goal_envelope: GoalEnvelope, context: Any | None) -> str:
    configured = goal_envelope.constraints.get("planner_mode", _DEFAULT_PLANNER_MODE)
    if context is not None:
        services = getattr(context, "services", {}) or {}
        configured = services.get("planner_mode", configured)
    mode = str(configured or _DEFAULT_PLANNER_MODE).strip() or _DEFAULT_PLANNER_MODE
    return mode


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
            PlannedNode(
                node_id="evidence_synthesis",
                node_type="evidence_synthesis",
                reason="Need to synthesize the resolved target evidence into an explanation",
                depends_on=["chunked_file_read"],
                success_criteria=["produce a grounded target explanation"],
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
            inputs={
                "goal": goal_envelope.goal,
                "intent": goal_envelope.intent,
                "fallback_reason": "unsupported_intent",
                "compatibility_mode": True,
                "source_loop": "workspace_backend",
            },
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


def _call_model_planner(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, context: Any | None) -> dict[str, Any] | None:
    application_context = get_application_context(context)
    if application_context is None:
        return None
    runtime = resolve_model_runtime(application_context, "planner")
    llm_client = runtime.client if runtime is not None else application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else application_context.llm_model
    if llm_client is None or not model_name:
        return None
    response = chat_once(
        llm_client,
        ChatRequest(
            model=model_name,
            messages=[
                ChatMessage(
                    role="system",
                    content=build_subgraph_planner_system_prompt(),
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "goal": goal_envelope.goal,
                            "intent": goal_envelope.intent,
                            "target_hints": goal_envelope.target_hints,
                            "success_criteria": goal_envelope.success_criteria,
                            "iteration": graph_state.current_iteration + 1,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            temperature=0.0,
            max_tokens=600,
        ),
    )
    return json.loads(extract_json_block(str(response.content or "")))


def _normalize_model_planned_nodes(payload: dict[str, Any], iteration: int, max_dynamic_nodes: int) -> tuple[list[PlannedNode], list[WorkflowEdge]]:
    raw_nodes = list(payload.get("nodes") or [])[:max_dynamic_nodes]
    if not raw_nodes:
        raise ValueError("model planner returned no nodes")
    node_id_map = {str(node.get("node_id") or "").strip(): f"{str(node.get('node_id') or '').strip()}_{iteration}" for node in raw_nodes}
    if any(not source_id for source_id in node_id_map):
        raise ValueError("model planner returned empty node id")
    if len(node_id_map) != len(raw_nodes):
        raise ValueError("model planner returned duplicate node ids")

    nodes: list[PlannedNode] = []
    for item in raw_nodes:
        source_id = str(item.get("node_id") or "").strip()
        node_type = str(item.get("node_type") or "").strip()
        if node_type not in ALLOWED_DYNAMIC_NODE_TYPES:
            raise ValueError(f"unsupported planned node type: {node_type}")
        depends_on = [str(dep).strip() for dep in item.get("depends_on") or [] if str(dep).strip()]
        unknown_dependencies = [dep for dep in depends_on if dep not in node_id_map]
        if unknown_dependencies:
            raise ValueError(f"unknown dependencies: {', '.join(unknown_dependencies)}")
        success_criteria = [str(criterion).strip() for criterion in item.get("success_criteria") or [] if str(criterion).strip()]
        if not success_criteria:
            raise ValueError("model planner returned node without success criteria")
        nodes.append(
            PlannedNode(
                node_id=node_id_map[source_id],
                node_type=node_type,
                reason=str(item.get("reason") or "").strip() or f"Execute {node_type}",
                inputs=dict(item.get("inputs") or {}),
                depends_on=[node_id_map[dep] for dep in depends_on],
                success_criteria=success_criteria,
            )
        )

    edges = [WorkflowEdge(source=dependency, target=node.node_id) for node in nodes for dependency in node.depends_on]
    return nodes, edges


def _plan_next_subgraph_with_model(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, context: Any | None) -> PlannedSubgraph:
    payload = _call_model_planner(goal_envelope, graph_state, context)
    if payload is None:
        raise ValueError("model planner unavailable")
    max_dynamic_nodes = _max_dynamic_nodes(goal_envelope, context)
    iteration = graph_state.current_iteration + 1
    nodes, edges = _normalize_model_planned_nodes(payload, iteration, max_dynamic_nodes)
    return PlannedSubgraph(
        iteration=iteration,
        planner_summary=str(payload.get("planner_summary") or f"Model plan iteration {iteration} for {goal_envelope.intent}"),
        nodes=nodes,
        edges=edges,
        metadata={"planner": "model_v1", "max_dynamic_nodes": max_dynamic_nodes, "strategy": "model", "model_role": "planner"},
    )


def _plan_next_subgraph_deterministically(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, context: Any | None) -> PlannedSubgraph:
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
        metadata={"planner": "deterministic_v2", "max_dynamic_nodes": max_dynamic_nodes, "strategy": "deterministic", "model_role": "planner"},
    )


def plan_next_subgraph(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, context: Any | None) -> PlannedSubgraph:
    if _planner_mode(goal_envelope, context) == "deterministic":
        return _plan_next_subgraph_deterministically(goal_envelope, graph_state, context)
    try:
        return _plan_next_subgraph_with_model(goal_envelope, graph_state, context)
    except Exception as exc:
        fallback = _plan_next_subgraph_deterministically(goal_envelope, graph_state, context)
        fallback.metadata = {
            **dict(fallback.metadata or {}),
            "strategy": "fallback",
            "fallback_reason": str(exc),
        }
        return fallback
