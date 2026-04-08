from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent_runtime_framework.api.process_trace import emit_process_event
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.llm.structured_output_repair import parse_json_object, repair_structured_output, repair_structured_output_until_valid
from agent_runtime_framework.workflow.llm.access import get_application_context
from agent_runtime_framework.workflow.memory.views import build_planner_memory_view
from agent_runtime_framework.workflow.state.models import AgentGraphState, GRAPH_NATIVE_WRITE_NODE_TYPES, GoalEnvelope, PlannedNode, PlannedSubgraph, WorkflowEdge
from agent_runtime_framework.workflow.state.models import build_agent_graph_execution_summary
from agent_runtime_framework.workflow.planning.prompts import build_subgraph_planner_system_prompt

ALLOWED_DYNAMIC_NODE_TYPES = {
    "interpret_target",
    "plan_search",
    "plan_read",
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
    "final_response",
    *GRAPH_NATIVE_WRITE_NODE_TYPES,
}

_DEFAULT_MAX_DYNAMIC_NODES = 3
_MAX_FAILURE_HISTORY = 2
_MAX_ITERATION_SUMMARIES = 2
_MAX_ATTEMPTED_STRATEGIES = 4
_SEMANTIC_FOUNDATION_ORDER = ("interpret_target", "plan_search", "plan_read")
_JUDGE_ROUTE_TYPE_ALIASES = {
    "plan_read": {"chunked_file_read"},
    "verification": {"verification_step"},
}


def _normalize_node_inputs(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


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


def _enforce_judge_route_contract(nodes: list[PlannedNode], decision: Any | None) -> None:
    payload = _judge_feedback_payload(decision) or {}
    allowed = _expand_route_node_types(payload.get("allowed_next_node_types", []) or [])
    blocked = _expand_route_node_types(payload.get("blocked_next_node_types", []) or [])
    if not allowed and not blocked:
        return
    semantic_foundation = set(_SEMANTIC_FOUNDATION_ORDER)
    node_types = [str(node.node_type).strip() for node in nodes]
    if allowed:
        invalid = [node_type for node_type in node_types if node_type not in semantic_foundation and node_type not in allowed]
        if invalid:
            raise ValueError(f"planned node types violate judge allowed_next_node_types: {', '.join(invalid)}")
    if blocked:
        forbidden = [node_type for node_type in node_types if node_type in blocked]
        if forbidden:
            raise ValueError(f"planned node types violate judge blocked_next_node_types: {', '.join(forbidden)}")


def _expand_route_node_types(values: list[Any]) -> set[str]:
    expanded: set[str] = set()
    queue = [str(item).strip() for item in values if str(item).strip()]
    while queue:
        node_type = queue.pop(0)
        if node_type in expanded:
            continue
        expanded.add(node_type)
        queue.extend(sorted(_JUDGE_ROUTE_TYPE_ALIASES.get(node_type, set()) - expanded))
    return expanded


def _has_semantic_value(memory: dict[str, Any], key: str, required_field: str | None = None) -> bool:
    value = dict(memory.get(key) or {})
    if not value:
        return False
    if required_field is None:
        return True
    return bool(str(value.get(required_field) or "").strip())


def _semantic_foundation_targets(nodes: list[PlannedNode]) -> dict[str, bool]:
    node_types = {node.node_type for node in nodes}
    needs_interpret = bool(node_types & {"target_resolution", "plan_search", "content_search", "plan_read", "chunked_file_read"})
    needs_search = bool(node_types & {"content_search", "plan_read", "chunked_file_read"})
    needs_read = bool(node_types & {"chunked_file_read"})
    return {
        "interpret_target": needs_interpret,
        "plan_search": needs_search,
        "plan_read": needs_read,
    }


def _inject_semantic_foundation(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, nodes: list[PlannedNode], iteration: int) -> list[PlannedNode]:
    semantic_memory = dict(graph_state.memory_state.semantic_memory or {})
    existing_by_type = {node.node_type: node for node in nodes}
    required = _semantic_foundation_targets(nodes)
    foundation_nodes: list[PlannedNode] = []

    def add_foundation(node_type: str, *, depends_on: list[str]) -> str | None:
        if node_type in existing_by_type:
            node = existing_by_type[node_type]
            for dependency in depends_on:
                if dependency and dependency not in node.depends_on:
                    node.depends_on.append(dependency)
            return node.node_id
        if node_type == "interpret_target" and _has_semantic_value(semantic_memory, "interpreted_target", "preferred_path"):
            return None
        if node_type == "plan_search" and _has_semantic_value(semantic_memory, "search_plan", "search_goal"):
            return None
        if node_type == "plan_read" and _has_semantic_value(semantic_memory, "read_plan", "target_path"):
            return None
        node_id = f"{node_type}_{iteration}"
        if any(existing.node_id == node_id for existing in [*foundation_nodes, *nodes]):
            node_id = f"{node_type}_foundation_{iteration}"
        reason_map = {
            "interpret_target": "Prepare target semantics before execution",
            "plan_search": "Prepare search semantics before execution",
            "plan_read": "Prepare read semantics before execution",
        }
        success_map = {
            "interpret_target": ["capture target constraints"],
            "plan_search": ["define search plan"],
            "plan_read": ["define read plan"],
        }
        node = PlannedNode(
            node_id=node_id,
            node_type=node_type,
            reason=reason_map[node_type],
            depends_on=[dependency for dependency in depends_on if dependency],
            success_criteria=success_map[node_type],
        )
        foundation_nodes.append(node)
        existing_by_type[node_type] = node
        return node_id

    interpret_id = None
    search_id = None
    read_id = None
    if required["interpret_target"]:
        interpret_id = add_foundation("interpret_target", depends_on=[])
    if required["plan_search"]:
        search_id = add_foundation("plan_search", depends_on=[interpret_id] if interpret_id else [])
    if required["plan_read"]:
        prior = search_id or interpret_id
        read_id = add_foundation("plan_read", depends_on=[prior] if prior else [])

    for node in nodes:
        if node.node_type == "target_resolution" and interpret_id and interpret_id not in node.depends_on:
            node.depends_on.append(interpret_id)
        if node.node_type == "content_search":
            prerequisite = search_id or interpret_id
            if prerequisite and prerequisite not in node.depends_on:
                node.depends_on.append(prerequisite)
        if node.node_type == "plan_read":
            prerequisite = search_id or interpret_id
            if prerequisite and prerequisite not in node.depends_on:
                node.depends_on.append(prerequisite)
        if node.node_type == "chunked_file_read":
            prerequisite = read_id or search_id or interpret_id
            if prerequisite and prerequisite not in node.depends_on:
                node.depends_on.append(prerequisite)

    return [*foundation_nodes, *nodes]


def _execution_summary(graph_state: AgentGraphState) -> dict[str, Any]:
    payload = graph_state.aggregated_payload
    summary = build_agent_graph_execution_summary(graph_state)
    summary["evidence_count"] = (
        len(payload.get("evidence_items", []) or [])
        + len(payload.get("chunks", []) or [])
        + len(payload.get("facts", []) or [])
    )
    return summary


def _repair_recorder(graph_state: AgentGraphState, context: Any | None = None):
    def _record(event: dict[str, Any]) -> None:
        payload = dict(event)
        graph_state.repair_history.append(payload)
        sink = getattr(context, "process_sink", None) if context is not None else None
        emit_process_event(
            sink,
            {
                "kind": "plan",
                "status": "completed" if bool(payload.get("success")) else "started",
                "title": "内部修复规划输出" if bool(payload.get("success")) else "尝试修复规划输出",
                "detail": f"{str(payload.get('contract_kind') or 'subgraph_plan')} · {int(payload.get('attempts_used') or 0)} 次尝试",
                "node_type": "repair",
                "metadata": {"repair": True, **payload},
            },
        )

    return _record


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _compact_attempted_strategies(graph_state: AgentGraphState) -> list[str]:
    return _dedupe_preserve_order(list(graph_state.attempted_strategies))[-_MAX_ATTEMPTED_STRATEGIES:]


def _compact_failure_history(graph_state: AgentGraphState) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in graph_state.failure_history[-_MAX_FAILURE_HISTORY:]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "iteration": int(item.get("iteration") or 0),
                "status": str(item.get("status") or ""),
                "reason": str(item.get("reason") or ""),
                "missing_evidence": [str(value) for value in item.get("missing_evidence", []) or [] if str(value).strip()],
                "diagnosis": dict(item.get("diagnosis") or {}),
                "strategy_guidance": dict(item.get("strategy_guidance") or {}),
            }
        )
    return compacted


def _compact_iteration_summaries(graph_state: AgentGraphState) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in graph_state.iteration_summaries[-_MAX_ITERATION_SUMMARIES:]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "iteration": int(item.get("iteration") or 0),
                "planner_summary": str(item.get("planner_summary") or ""),
                "judge_status": str(item.get("judge_status") or ""),
                "missing_evidence": [str(value) for value in item.get("missing_evidence", []) or [] if str(value).strip()],
                "diagnosis": dict(item.get("diagnosis") or {}),
            }
        )
    return compacted


def _ineffective_actions(graph_state: AgentGraphState) -> list[str]:
    actions: list[str] = []
    iteration_lookup = {
        int(item.get("iteration") or 0): str(item.get("planner_summary") or "").strip()
        for item in graph_state.iteration_summaries
        if isinstance(item, dict)
    }
    for failure in _compact_failure_history(graph_state):
        if failure["status"] == "accepted":
            continue
        summary = iteration_lookup.get(int(failure.get("iteration") or 0), "")
        if summary:
            actions.append(summary)
    return _dedupe_preserve_order(actions)


def _compact_execution_summary(graph_state: AgentGraphState) -> dict[str, Any]:
    summary = dict(_execution_summary(graph_state))
    quality_signals = [dict(item) for item in summary.get("quality_signals", []) or [] if isinstance(item, dict)]
    if quality_signals:
        summary["quality_signals"] = quality_signals[:3]
    if "attempted_strategies" in summary:
        summary["attempted_strategies"] = _compact_attempted_strategies(graph_state)
    latest_failure = summary.get("latest_failure")
    if isinstance(latest_failure, dict):
        compact_failures = _compact_failure_history(graph_state)
        summary["latest_failure"] = compact_failures[-1] if compact_failures else latest_failure
    return summary


def _planner_context_payload(goal_envelope: GoalEnvelope, graph_state: AgentGraphState) -> dict[str, Any]:
    return {
        "goal": goal_envelope.goal,
        "intent": goal_envelope.intent,
        "target_hints": goal_envelope.target_hints,
        "success_criteria": goal_envelope.success_criteria,
        "iteration": graph_state.current_iteration + 1,
        "latest_judge_decision": _judge_feedback_payload(_latest_judge_decision(graph_state)),
        "execution_summary": _compact_execution_summary(graph_state),
        "planner_memory_view": build_planner_memory_view(graph_state),
    }


def _resolved_target_payload(graph_state: AgentGraphState) -> dict[str, Any]:
    payload = dict(graph_state.aggregated_payload or {})
    artifacts = dict(payload.get("artifacts") or {})
    resolved_targets = artifacts.get("resolved_target") or []
    if resolved_targets and isinstance(resolved_targets[-1], dict):
        return dict(resolved_targets[-1])
    return {}


def _should_use_constrained_read_path(goal_envelope: GoalEnvelope, graph_state: AgentGraphState) -> bool:
    if str(goal_envelope.intent or "").strip() != "file_read":
        return False
    semantic_memory = dict(graph_state.memory_state.semantic_memory or {})
    interpreted_target = dict(semantic_memory.get("interpreted_target") or {})
    if bool(interpreted_target.get("confirmed")) and str(interpreted_target.get("preferred_path") or "").strip():
        return True
    confirmed_targets = [str(item).strip() for item in semantic_memory.get("confirmed_targets", []) or [] if str(item).strip()]
    if len(confirmed_targets) == 1:
        return True
    resolved_target = _resolved_target_payload(graph_state)
    return str(resolved_target.get("resolution_status") or "").strip() == "resolved"


def _constrained_read_subgraph(goal_envelope: GoalEnvelope, graph_state: AgentGraphState) -> PlannedSubgraph:
    iteration = graph_state.current_iteration + 1
    nodes = [
        PlannedNode(
            node_id=f"plan_read_{iteration}",
            node_type="plan_read",
            reason="Confirmed file target allows direct read planning",
            success_criteria=["define read plan for the confirmed target"],
        ),
        PlannedNode(
            node_id=f"chunked_file_read_{iteration}",
            node_type="chunked_file_read",
            reason="Read the confirmed file directly",
            depends_on=[f"plan_read_{iteration}"],
            success_criteria=["collect grounded evidence from the confirmed file"],
        ),
        PlannedNode(
            node_id=f"final_response_{iteration}",
            node_type="final_response",
            reason="Respond after reading the confirmed file",
            depends_on=[f"chunked_file_read_{iteration}"],
            success_criteria=["deliver grounded final response"],
        ),
    ]
    edges = [WorkflowEdge(source=node.depends_on[0], target=node.node_id) for node in nodes if node.depends_on]
    return PlannedSubgraph(
        iteration=iteration,
        planner_summary=f"Constrained confirmed-file read path for iteration {iteration}",
        nodes=nodes,
        edges=edges,
        metadata={"planner": "constrained_file_read_v1", "max_dynamic_nodes": len(nodes)},
    )


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
    request_payload = _planner_context_payload(goal_envelope, graph_state)
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
                    content=json.dumps(request_payload, ensure_ascii=False),
                ),
            ],
            temperature=0.0,
            max_tokens=600,
        ),
    )
    raw_content = str(response.content or "")
    parsed, parse_error = parse_json_object(raw_content)
    if isinstance(parsed, dict):
        return parsed
    repaired = repair_structured_output(
        context,
        role="planner",
        contract_kind="subgraph_plan",
        required_fields=["planner_summary", "nodes"],
        original_output=raw_content,
        validation_error=parse_error or "invalid model response",
        request_payload=request_payload,
        extra_instructions="nodes must be a non-empty array of planned workflow nodes.",
        on_record=_repair_recorder(graph_state, context),
    )
    if isinstance(repaired, dict):
        return repaired
    raise ValueError("model planner returned invalid json")


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
                inputs=_normalize_node_inputs(item.get("inputs")),
                depends_on=[node_id_map[dep] for dep in depends_on],
                success_criteria=success_criteria,
                requires_approval=bool(item.get("requires_approval")),
            )
        )

    edges = [WorkflowEdge(source=dependency, target=node.node_id) for node in nodes for dependency in node.depends_on]
    return nodes, edges


def _validate_subgraph_plan_payload(
    payload: Any,
    *,
    goal_envelope: GoalEnvelope,
    graph_state: AgentGraphState,
    iteration: int,
    max_dynamic_nodes: int,
) -> str | None:
    if not isinstance(payload, dict):
        return "model planner returned invalid json"
    try:
        nodes, _ = _normalize_model_planned_nodes(payload, iteration, max_dynamic_nodes)
        nodes = _inject_semantic_foundation(goal_envelope, graph_state, nodes, iteration)
        _enforce_judge_route_contract(nodes, _latest_judge_decision(graph_state))
    except (ValueError, TypeError) as exc:
        return str(exc)
    return None


def plan_next_subgraph(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, context: Any | None) -> PlannedSubgraph:
    if _should_use_constrained_read_path(goal_envelope, graph_state):
        return _constrained_read_subgraph(goal_envelope, graph_state)
    payload = _call_model_planner(goal_envelope, graph_state, context)
    if payload is None:
        raise ValueError("model planner unavailable")
    max_dynamic_nodes = _max_dynamic_nodes(goal_envelope, context)
    iteration = graph_state.current_iteration + 1
    request_payload = _planner_context_payload(goal_envelope, graph_state)
    validation_error = _validate_subgraph_plan_payload(
        payload,
        goal_envelope=goal_envelope,
        graph_state=graph_state,
        iteration=iteration,
        max_dynamic_nodes=max_dynamic_nodes,
    )
    if validation_error is not None:
        repaired = repair_structured_output_until_valid(
            context,
            role="planner",
            contract_kind="subgraph_plan",
            required_fields=["planner_summary", "nodes"],
            original_output=payload,
            request_payload=request_payload,
            validate=lambda candidate: _validate_subgraph_plan_payload(
                candidate,
                goal_envelope=goal_envelope,
                graph_state=graph_state,
                iteration=iteration,
                max_dynamic_nodes=max_dynamic_nodes,
            ),
            extra_instructions=(
                "Return only node types permitted by latest_judge_decision. "
                "Do not emit blocked node types. "
                "If verification is allowed, verification_step is also acceptable. "
                "If plan_read is allowed, chunked_file_read is also acceptable."
            ),
            on_record=_repair_recorder(graph_state, context),
        )
        if isinstance(repaired, dict):
            payload = repaired
    nodes, edges = _normalize_model_planned_nodes(payload, iteration, max_dynamic_nodes)
    nodes = _inject_semantic_foundation(goal_envelope, graph_state, nodes, iteration)
    _enforce_judge_route_contract(nodes, _latest_judge_decision(graph_state))
    edges = [WorkflowEdge(source=dependency, target=node.node_id) for node in nodes for dependency in node.depends_on]
    return PlannedSubgraph(
        iteration=iteration,
        planner_summary=str(payload.get("planner_summary") or f"Model plan iteration {iteration} for {goal_envelope.intent}"),
        nodes=nodes,
        edges=edges,
        metadata={"planner": "model_v1", "max_dynamic_nodes": max_dynamic_nodes},
    )
