from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.agents.workspace_backend.prompting import extract_json_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.decomposition import decompose_goal
from agent_runtime_framework.workflow.models import GoalSpec, SubTaskSpec, WorkflowEdge, WorkflowGraph, WorkflowNode


_NATIVE_NODE_TYPES = {"repository_explainer", "file_reader", "aggregate_results", "final_response", "verification", "approval_gate"}
_SUPPORTED_NODE_TYPES = _NATIVE_NODE_TYPES | {"workspace_subtask"}
_MODEL_ONLY_FLAGS = {"workflow_model_only", "workflow_graph_model_only"}


def build_workflow_graph(goal: GoalSpec, context: Any | None = None) -> WorkflowGraph:
    llm_graph = _build_graph_with_model(goal, context=context)
    if llm_graph is not None:
        return _normalize_graph(llm_graph, goal)
    if _is_model_only(context):
        raise ValueError("workflow graph compilation is model-only in this environment")
    return _build_graph_deterministically(goal, context=context)


def _build_graph_with_model(goal: GoalSpec, *, context: Any | None) -> WorkflowGraph | None:
    if context is None:
        return None
    if not bool(getattr(context, "services", {}).get("model_first_workflow_graph_builder")):
        return None

    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        return None

    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You compile a workflow graph. Return JSON only with keys nodes and edges. "
                            "Each node needs: node_id, node_type, task_profile, dependencies, requires_approval, retry_limit, metadata. "
                            "Use node_type to choose the executor path directly. Supported node types include "
                            "repository_explainer, file_reader, workspace_subtask, verification, approval_gate, aggregate_results, final_response. "
                            "Each edge needs: source, target, condition, metadata."
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "original_goal": goal.original_goal,
                                "primary_intent": goal.primary_intent,
                                "requires_repository_overview": goal.requires_repository_overview,
                                "requires_file_read": goal.requires_file_read,
                                "requires_final_synthesis": goal.requires_final_synthesis,
                                "target_paths": goal.target_paths,
                                "metadata": goal.metadata,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=800,
            ),
        )
    except Exception:
        return None

    try:
        parsed = json.loads(extract_json_block(str(response.content or "")))
    except Exception:
        return None

    nodes: list[WorkflowNode] = []
    for item in parsed.get("nodes") or []:
        if not isinstance(item, dict):
            return None
        node_id = str(item.get("node_id") or "").strip()
        node_type = str(item.get("node_type") or "").strip()
        if not node_id or not node_type:
            return None
        nodes.append(
            WorkflowNode(
                node_id=node_id,
                node_type=node_type,
                dependencies=[str(dep).strip() for dep in item.get("dependencies") or [] if str(dep).strip()],
                task_profile=(str(item.get("task_profile")).strip() if item.get("task_profile") is not None else None),
                requires_approval=bool(item.get("requires_approval", False)),
                retry_limit=int(item.get("retry_limit") or 0),
                metadata=dict(item.get("metadata") or {}),
            )
        )

    edges: list[WorkflowEdge] = []
    for item in parsed.get("edges") or []:
        if not isinstance(item, dict):
            return None
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if not source or not target:
            return None
        edges.append(
            WorkflowEdge(
                source=source,
                target=target,
                condition=(str(item.get("condition")).strip() if item.get("condition") is not None else None),
                metadata=dict(item.get("metadata") or {}),
            )
        )

    if not nodes:
        return None
    return WorkflowGraph(nodes=nodes, edges=edges, metadata={"goal": goal.original_goal, "source": "model"})


def _build_graph_deterministically(goal: GoalSpec, context: Any | None = None) -> WorkflowGraph:
    if goal.primary_intent not in {"file_read", "repository_overview", "compound"}:
        return build_workspace_subtask_graph(goal)
    subtasks = decompose_goal(goal, context=context)
    executable_subtasks = [subtask for subtask in subtasks if subtask.task_profile != "final_synthesis"]
    if not executable_subtasks:
        return build_workspace_subtask_graph(goal)

    nodes = [_node_for_subtask(subtask, goal) for subtask in executable_subtasks]
    edges = [WorkflowEdge(source=dependency, target=subtask.task_id) for subtask in executable_subtasks for dependency in subtask.depends_on]
    return _compose_graph(nodes, edges, goal, source="fallback")


def build_workspace_subtask_graph(goal: GoalSpec) -> WorkflowGraph:
    node = WorkflowNode(
        node_id="workspace_subtask",
        node_type="workspace_subtask",
        task_profile=goal.primary_intent,
        metadata={
            "goal": goal.original_goal,
            "task_profile": goal.primary_intent,
            "executor_kind": "workspace_subtask",
            **dict(goal.metadata or {}),
        },
    )
    return _compose_graph([node], [], goal, source="workspace_subtask_fallback")


def _node_for_subtask(subtask: SubTaskSpec, goal: GoalSpec) -> WorkflowNode:
    metadata = dict(subtask.metadata)
    if subtask.target:
        metadata.setdefault("target_path", subtask.target)
    if subtask.task_profile in _NATIVE_NODE_TYPES:
        metadata.setdefault("executor_kind", "native")
        return WorkflowNode(
            node_id=subtask.task_id,
            node_type=subtask.task_profile,
            dependencies=list(subtask.depends_on),
            task_profile=subtask.task_profile,
            metadata=metadata,
        )
    metadata.setdefault("goal", goal.original_goal)
    metadata.setdefault("task_profile", subtask.task_profile)
    metadata.setdefault("executor_kind", "workspace_subtask")
    return WorkflowNode(
        node_id=subtask.task_id,
        node_type="workspace_subtask",
        dependencies=list(subtask.depends_on),
        task_profile=subtask.task_profile,
        metadata=metadata,
    )


def _compose_graph(nodes: list[WorkflowNode], edges: list[WorkflowEdge], goal: GoalSpec, *, source: str) -> WorkflowGraph:
    if not nodes:
        raise ValueError("workflow graph requires at least one executable node")

    executable_node_ids = [node.node_id for node in nodes]
    anchor = executable_node_ids[0]

    if len(executable_node_ids) > 1:
        aggregate_node = WorkflowNode(
            node_id="aggregate_results",
            node_type="aggregate_results",
            dependencies=list(executable_node_ids),
            metadata={"executor_kind": "native"},
        )
        nodes.append(aggregate_node)
        for node_id in executable_node_ids:
            edges.append(WorkflowEdge(source=node_id, target=aggregate_node.node_id))
        anchor = aggregate_node.node_id

    if goal.metadata.get("requires_verification"):
        verification_node = WorkflowNode(
            node_id="verification",
            node_type="verification",
            dependencies=[anchor],
            task_profile="verification",
            metadata={"executor_kind": "native", **dict(goal.metadata or {})},
        )
        nodes.append(verification_node)
        edges.append(WorkflowEdge(source=anchor, target=verification_node.node_id))
        anchor = verification_node.node_id

    if goal.metadata.get("requires_approval"):
        approval_node = WorkflowNode(
            node_id="approval_gate",
            node_type="approval_gate",
            dependencies=[anchor],
            task_profile="approval_gate",
            requires_approval=True,
            metadata={"executor_kind": "native", **dict(goal.metadata or {})},
        )
        nodes.append(approval_node)
        edges.append(WorkflowEdge(source=anchor, target=approval_node.node_id))
        anchor = approval_node.node_id

    final_node = WorkflowNode(
        node_id="final_response",
        node_type="final_response",
        dependencies=[anchor],
        metadata={"executor_kind": "native"},
    )
    nodes.append(final_node)
    edges.append(WorkflowEdge(source=anchor, target=final_node.node_id))
    return _normalize_graph(WorkflowGraph(nodes=nodes, edges=edges, metadata={"goal": goal.original_goal, "source": source}), goal)


def _normalize_graph(graph: WorkflowGraph, goal: GoalSpec) -> WorkflowGraph:
    dependencies_by_node = {node.node_id: list(node.dependencies) for node in graph.nodes}
    for edge in graph.edges:
        target_dependencies = dependencies_by_node.setdefault(edge.target, [])
        if edge.source not in target_dependencies:
            target_dependencies.append(edge.source)
    normalized_nodes: list[WorkflowNode] = []
    for node in graph.nodes:
        metadata = dict(node.metadata or {})
        if node.task_profile and node.node_type == "workspace_subtask":
            metadata.setdefault("task_profile", node.task_profile)
        if node.node_type in _NATIVE_NODE_TYPES:
            metadata.setdefault("executor_kind", "native")
        elif node.node_type == "workspace_subtask":
            metadata.setdefault("executor_kind", "workspace_subtask")
            metadata.setdefault("goal", goal.original_goal)
        normalized_nodes.append(
            WorkflowNode(
                node_id=node.node_id,
                node_type=node.node_type,
                dependencies=dependencies_by_node.get(node.node_id, []),
                task_profile=node.task_profile,
                requires_approval=node.requires_approval,
                retry_limit=node.retry_limit,
                metadata=metadata,
            )
        )
    return WorkflowGraph(nodes=normalized_nodes, edges=list(graph.edges), metadata=dict(graph.metadata or {}))


def _is_model_only(context: Any | None) -> bool:
    if context is None:
        return False
    services = getattr(context, "services", {}) or {}
    return any(bool(services.get(flag)) for flag in _MODEL_ONLY_FLAGS)
