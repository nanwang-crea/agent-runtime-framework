from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.agents.codex.prompting import extract_json_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.decomposition import decompose_goal
from agent_runtime_framework.workflow.models import GoalSpec, WorkflowEdge, WorkflowGraph, WorkflowNode


def build_workflow_graph(goal: GoalSpec, context: Any | None = None) -> WorkflowGraph:
    llm_graph = _build_graph_with_model(goal, context=context)
    if llm_graph is not None:
        return llm_graph
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
                max_tokens=600,
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
    subtasks = decompose_goal(goal, context=context)
    executable_subtasks = [subtask for subtask in subtasks if subtask.task_profile != "final_synthesis"]
    nodes: list[WorkflowNode] = []
    edges: list[WorkflowEdge] = []

    for subtask in executable_subtasks:
        metadata = dict(subtask.metadata)
        if subtask.target:
            metadata.setdefault("target_path", subtask.target)
        nodes.append(
            WorkflowNode(
                node_id=subtask.task_id,
                node_type=subtask.task_profile,
                dependencies=list(subtask.depends_on),
                task_profile=subtask.task_profile,
                metadata=metadata,
            )
        )
        for dependency in subtask.depends_on:
            edges.append(WorkflowEdge(source=dependency, target=subtask.task_id))

    if len(executable_subtasks) == 1:
        final_node = WorkflowNode(node_id="final_response", node_type="final_response")
        nodes.append(final_node)
        edges.append(WorkflowEdge(source=executable_subtasks[0].task_id, target=final_node.node_id))
    elif executable_subtasks:
        aggregate_node = WorkflowNode(
            node_id="aggregate_results",
            node_type="aggregate_results",
            dependencies=[subtask.task_id for subtask in executable_subtasks],
        )
        final_node = WorkflowNode(
            node_id="final_response",
            node_type="final_response",
            dependencies=[aggregate_node.node_id],
        )
        nodes.append(aggregate_node)
        nodes.append(final_node)
        for subtask in executable_subtasks:
            edges.append(WorkflowEdge(source=subtask.task_id, target=aggregate_node.node_id))
        edges.append(WorkflowEdge(source=aggregate_node.node_id, target=final_node.node_id))

    if goal.metadata.get("requires_verification") and executable_subtasks:
        verification_node = WorkflowNode(node_id="verification", node_type="verification")
        nodes.append(verification_node)
        anchor = "aggregate_results" if any(node.node_id == "aggregate_results" for node in nodes) else executable_subtasks[-1].task_id
        edges.append(WorkflowEdge(source=anchor, target=verification_node.node_id))

    return WorkflowGraph(nodes=nodes, edges=edges, metadata={"goal": goal.original_goal, "source": "fallback"})
