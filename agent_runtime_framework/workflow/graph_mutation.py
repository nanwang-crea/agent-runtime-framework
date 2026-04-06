from __future__ import annotations

from copy import deepcopy

from agent_runtime_framework.workflow.models import PlannedSubgraph, WorkflowEdge, WorkflowGraph, WorkflowNode


def append_subgraph(graph: WorkflowGraph, subgraph: PlannedSubgraph, *, after_node_id: str) -> WorkflowGraph:
    existing_node_ids = {node.node_id for node in graph.nodes}
    appended_node_ids = [node.node_id for node in subgraph.nodes]
    duplicates = sorted(existing_node_ids.intersection(appended_node_ids))
    if duplicates:
        raise ValueError(f"duplicate node_id in appended subgraph: {', '.join(duplicates)}")

    anchor_index = next((index for index, node in enumerate(graph.nodes) if node.node_id == after_node_id), None)
    if anchor_index is None:
        raise ValueError(f"anchor node not found: {after_node_id}")

    appended_nodes = [
        WorkflowNode(
            node_id=node.node_id,
            node_type=node.node_type,
            dependencies=list(node.depends_on),
            requires_approval=node.requires_approval,
            metadata={"planned": True, **dict(node.inputs or {})},
        )
        for node in subgraph.nodes
    ]
    inserted_nodes = list(graph.nodes)
    inserted_nodes[anchor_index + 1 : anchor_index + 1] = appended_nodes

    inserted_edges = list(graph.edges)
    if appended_nodes:
        inserted_edges.append(WorkflowEdge(source=after_node_id, target=appended_nodes[0].node_id))
    inserted_edges.extend(deepcopy(subgraph.edges))

    metadata = dict(graph.metadata or {})
    append_history = list(metadata.get("append_history") or [])
    append_history.append(
        {
            "iteration": subgraph.iteration,
            "parent_judge_id": str(subgraph.metadata.get("parent_judge_id") or after_node_id),
            "planner_summary": subgraph.planner_summary,
            "appended_node_ids": appended_node_ids,
        }
    )
    metadata["append_history"] = append_history

    return WorkflowGraph(nodes=inserted_nodes, edges=inserted_edges, metadata=metadata)
