from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.llm_access import get_application_context
from agent_runtime_framework.workflow.models import AgentGraphState, GRAPH_NATIVE_WRITE_NODE_TYPES, GoalEnvelope, PlannedNode, PlannedSubgraph, WorkflowEdge
from agent_runtime_framework.workflow.planner_prompts import build_subgraph_planner_system_prompt
from agent_runtime_framework.workflow.prompting import extract_json_block

ALLOWED_DYNAMIC_NODE_TYPES = {
    "target_resolution",
    "workspace_discovery",
    "content_search",
    "chunked_file_read",
    "tool_call",
    "clarification",
    "verification",
    "verification_step",
    "aggregate_results",
    "evidence_synthesis",
    *GRAPH_NATIVE_WRITE_NODE_TYPES,
}

_DEFAULT_MAX_DYNAMIC_NODES = 3
def _latest_judge_decision(graph_state: AgentGraphState) -> Any | None:
    if not graph_state.judge_history:
        return None
    return graph_state.judge_history[-1]


def _judge_feedback_payload(decision: Any | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    if hasattr(decision, "as_payload"):
        return dict(decision.as_payload())
    if isinstance(decision, dict):
        return dict(decision)
    return None


def _execution_summary(graph_state: AgentGraphState) -> dict[str, Any]:
    if graph_state.execution_summary:
        return dict(graph_state.execution_summary)
    payload = graph_state.aggregated_payload
    return {
        "current_iteration": graph_state.current_iteration,
        "appended_node_ids": list(graph_state.appended_node_ids),
        "summaries": list(payload.get("summaries", []) or []),
        "evidence_count": len(payload.get("evidence_items", []) or []) + len(payload.get("chunks", []) or []) + len(payload.get("facts", []) or []),
        "verification": dict(payload.get("verification") or {}) if isinstance(payload.get("verification"), dict) else None,
    }


def _max_dynamic_nodes(goal_envelope: GoalEnvelope, context: Any | None) -> int:
    configured = goal_envelope.constraints.get("max_dynamic_nodes", _DEFAULT_MAX_DYNAMIC_NODES)
    if context is not None:
        services = getattr(context, "services", {}) or {}
        configured = services.get("max_dynamic_nodes", configured)
    try:
        return max(1, min(int(configured), _DEFAULT_MAX_DYNAMIC_NODES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_DYNAMIC_NODES


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
                            "latest_judge_decision": _judge_feedback_payload(_latest_judge_decision(graph_state)),
                            "execution_summary": _execution_summary(graph_state),
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
                requires_approval=bool(item.get("requires_approval")),
            )
        )

    edges = [WorkflowEdge(source=dependency, target=node.node_id) for node in nodes for dependency in node.depends_on]
    return nodes, edges


def plan_next_subgraph(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, context: Any | None) -> PlannedSubgraph:
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
        metadata={"planner": "model_v1", "max_dynamic_nodes": max_dynamic_nodes},
    )
