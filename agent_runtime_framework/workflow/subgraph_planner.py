from __future__ import annotations

import json
import re
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
_DEFAULT_PLANNER_MODE = "model_with_fallback"
_PATH_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./-]+")
_REWRITE_PATTERN = re.compile(r"重写.*?为(?P<content>.+?)(?:[，,。]|并|$)")
_APPEND_PATTERN = re.compile(r"追加(?P<content>.+?)(?:[，,。]|并|$)")
_REPLACE_PATTERN = re.compile(r"把.*?中的\s*(?P<search>.+?)\s*替换(?:成|为)\s*(?P<replace>.+?)(?:[，,。]|并|$)")
_CREATE_WITH_CONTENT_PATTERN = re.compile(r"(?:加入|写入|填入)(?P<content>.+?)(?:[，,。]|$)")


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


def _node_from_replan_hint(decision: Any | None) -> PlannedNode | None:
    payload = _judge_feedback_payload(decision)
    if not payload:
        return None
    hint = dict(payload.get("replan_hint") or {})
    next_node_type = str(hint.get("next_node_type") or "").strip()
    if next_node_type == "verification_step":
        next_node_type = "verification"

    if next_node_type == "verification":
        verification_type = str(hint.get("verification_type") or "completion").strip() or "completion"
        return PlannedNode(
            node_id="verification",
            node_type="verification",
            reason=str(payload.get("reason") or "Need verification before the workflow can complete."),
            inputs={"verification_type": verification_type},
            success_criteria=["produce an explicit verification result"],
        )

    if next_node_type == "clarification":
        prompt = str(hint.get("prompt") or payload.get("reason") or "Please clarify the request.").strip()
        return PlannedNode(
            node_id="clarification",
            node_type="clarification",
            reason=str(payload.get("reason") or "Need clarification before replanning."),
            inputs={"prompt": prompt},
            success_criteria=["collect the missing information"],
        )
    return None


def _nodes_from_replan_hint(decision: Any | None) -> list[PlannedNode]:
    payload = _judge_feedback_payload(decision)
    if not payload:
        return []
    hint = dict(payload.get("replan_hint") or {})
    recommended = [str(item).strip() for item in hint.get("recommended_next_actions", []) or hint.get("must_include", []) or [] if str(item).strip()]
    target_path = str(hint.get("target_path") or "").strip()
    content_brief = str(hint.get("content_brief") or "").strip()
    nodes: list[PlannedNode] = []
    previous_id: str | None = None
    for action in recommended:
        normalized = "verification" if action == "verification_step" else action
        if normalized == "write_file":
            node = PlannedNode(
                node_id="write_file",
                node_type="write_file",
                reason=str(payload.get("reason") or "Need to write the missing content."),
                inputs={"path": target_path, "content": content_brief},
                depends_on=([previous_id] if previous_id else []),
                success_criteria=["write the missing content to the target file"],
            )
        elif normalized == "verification":
            node = PlannedNode(
                node_id="verification",
                node_type="verification",
                reason=str(payload.get("reason") or "Need explicit verification before completion."),
                inputs={"verification_type": str(hint.get("verification_type") or "completion")},
                depends_on=([previous_id] if previous_id else []),
                success_criteria=["produce an explicit verification result"],
            )
        else:
            continue
        nodes.append(node)
        previous_id = node.node_id
    if nodes:
        return nodes
    single = _node_from_replan_hint(decision)
    return [single] if single is not None else []


def _extract_goal_paths(goal: str) -> list[str]:
    paths: list[str] = []
    for match in _PATH_TOKEN_PATTERN.findall(goal):
        candidate = match.strip("./")
        if not candidate:
            continue
        if "/" not in candidate and "." not in candidate:
            continue
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _build_filesystem_node(goal_envelope: GoalEnvelope) -> PlannedNode | None:
    goal = goal_envelope.goal
    goal_lower = goal.lower()
    target_hint = goal_envelope.target_hints[0] if goal_envelope.target_hints else ""
    paths = _extract_goal_paths(goal)

    if goal_envelope.intent == "dangerous_change":
        path = target_hint or (paths[0] if paths else "")
        if path:
            return PlannedNode(
                node_id="delete_path",
                node_type="delete_path",
                reason="Need an explicit graph-native delete step for this filesystem request",
                inputs={"path": path},
                success_criteria=["delete the requested workspace path"],
                requires_approval=True,
            )
        return None

    if goal_envelope.intent != "change_and_verify":
        return None

    is_move = any(token in goal for token in ("移动", "移到", "迁移", "重命名", "rename", "move"))
    is_create = any(token in goal for token in ("创建", "新建", "新增", "建立", "create"))

    if is_move:
        source_path = target_hint or (paths[0] if paths else "")
        destination_path = paths[1] if len(paths) > 1 else ""
        if source_path and destination_path:
            return PlannedNode(
                node_id="move_path",
                node_type="move_path",
                reason="Need an explicit graph-native move step for this filesystem request",
                inputs={"path": source_path, "destination_path": destination_path},
                success_criteria=["move the requested workspace path"],
            )

    if is_create and (target_hint or paths):
        path = target_hint or paths[0]
        kind = "directory" if any(token in goal_lower for token in ("目录", "文件夹", "directory", "folder")) else "file"
        return PlannedNode(
            node_id="create_path",
            node_type="create_path",
            reason="Need an explicit graph-native create step for this filesystem request",
            inputs={"path": path, "kind": kind},
            success_criteria=["create the requested workspace path"],
        )

    return None


def _build_create_with_content_nodes(goal_envelope: GoalEnvelope) -> list[PlannedNode]:
    if goal_envelope.intent != "change_and_verify":
        return []
    goal = goal_envelope.goal
    target_hint = goal_envelope.target_hints[0] if goal_envelope.target_hints else ""
    if not target_hint:
        return []
    is_create = any(token in goal for token in ("创建", "新建", "新增", "建立", "create"))
    content_match = _CREATE_WITH_CONTENT_PATTERN.search(goal)
    if not is_create or content_match is None:
        return []
    content = content_match.group("content").strip()
    if not content:
        return []
    return [
        PlannedNode(
            node_id="create_path",
            node_type="create_path",
            reason="Need to create the requested workspace path before writing content",
            inputs={"path": target_hint, "kind": "file"},
            success_criteria=["create the requested file"],
        ),
        PlannedNode(
            node_id="write_file",
            node_type="write_file",
            reason="Need to write the requested content into the new file",
            inputs={"path": target_hint, "content": content},
            depends_on=["create_path"],
            success_criteria=["write the requested content into the target file"],
        ),
    ]


def _build_text_edit_node(goal_envelope: GoalEnvelope) -> PlannedNode | None:
    if goal_envelope.intent != "change_and_verify":
        return None

    goal = goal_envelope.goal
    target_hint = goal_envelope.target_hints[0] if goal_envelope.target_hints else ""
    if not target_hint:
        return PlannedNode(
            node_id="clarification",
            node_type="clarification",
            reason="Need a specific target before performing a text edit",
            inputs={"prompt": "请明确要修改的文件路径以及修改方式。"},
            success_criteria=["collect the missing edit details"],
        )

    replace_match = _REPLACE_PATTERN.search(goal)
    if replace_match is not None:
        return PlannedNode(
            node_id="apply_patch",
            node_type="apply_patch",
            reason="Need a targeted replacement for this text-edit request",
            inputs={
                "path": target_hint,
                "search_text": replace_match.group("search").strip(),
                "replace_text": replace_match.group("replace").strip(),
            },
            success_criteria=["apply the requested targeted text replacement"],
        )

    rewrite_match = _REWRITE_PATTERN.search(goal)
    if rewrite_match is not None or "重写" in goal or "全文改写" in goal:
        return PlannedNode(
            node_id="write_file",
            node_type="write_file",
            reason="Need a full rewrite for this text-edit request",
            inputs={"path": target_hint, "content": (rewrite_match.group("content").strip() if rewrite_match is not None else "")},
            success_criteria=["rewrite the target file with the requested content"],
        )

    append_match = _APPEND_PATTERN.search(goal)
    if append_match is not None or "append" in goal.lower():
        return PlannedNode(
            node_id="append_text",
            node_type="append_text",
            reason="Need to append text for this text-edit request",
            inputs={"path": target_hint, "content": (append_match.group("content").strip() if append_match is not None else "")},
            success_criteria=["append the requested text to the target file"],
        )

    return PlannedNode(
        node_id="clarification",
        node_type="clarification",
        reason="Need a more specific edit instruction before changing text",
        inputs={"prompt": "请说明是重写、替换指定文本，还是追加内容。"},
        success_criteria=["collect the missing edit details"],
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


def _planner_mode(goal_envelope: GoalEnvelope, context: Any | None) -> str:
    configured = goal_envelope.constraints.get("planner_mode", _DEFAULT_PLANNER_MODE)
    if context is not None:
        services = getattr(context, "services", {}) or {}
        configured = services.get("planner_mode", configured)
    mode = str(configured or _DEFAULT_PLANNER_MODE).strip() or _DEFAULT_PLANNER_MODE
    return mode


def _candidate_nodes(goal_envelope: GoalEnvelope, graph_state: AgentGraphState) -> list[PlannedNode]:
    target_hint = goal_envelope.target_hints[0] if goal_envelope.target_hints else ""
    feedback_nodes = _nodes_from_replan_hint(_latest_judge_decision(graph_state))
    if feedback_nodes:
        return feedback_nodes
    create_with_content = _build_create_with_content_nodes(goal_envelope)
    if create_with_content:
        return create_with_content
    filesystem_node = _build_filesystem_node(goal_envelope)
    if filesystem_node is not None:
        return [filesystem_node]
    text_edit_node = _build_text_edit_node(goal_envelope)
    if text_edit_node is not None:
        return [text_edit_node]
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
            node_id="clarification",
            node_type="clarification",
            reason="Need a clearer workflow-native instruction for this unsupported request",
            inputs={"prompt": "请进一步明确你希望执行的工作区操作或目标结果。"},
            success_criteria=["collect enough detail to plan graph-native nodes"],
        )
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
    base_nodes = _candidate_nodes(goal_envelope, graph_state)[:max_dynamic_nodes]
    node_id_map = {node.node_id: f"{node.node_id}_{iteration}" for node in base_nodes}
    nodes = [
        PlannedNode(
            node_id=node_id_map[node.node_id],
            node_type=node.node_type,
            reason=node.reason,
            inputs=dict(node.inputs),
            depends_on=[node_id_map.get(dep, dep) for dep in node.depends_on],
            success_criteria=list(node.success_criteria),
            requires_approval=node.requires_approval,
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
