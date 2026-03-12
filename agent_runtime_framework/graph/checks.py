from __future__ import annotations


class GraphValidationError(ValueError):
    pass


def validate_graph(
    nodes: set[str],
    edges: dict[str, list[str]],
    conditional_edges: dict[str, list[str]],
    entry_point: str | None,
    finish_point: str | None,
) -> None:
    if not entry_point:
        raise GraphValidationError("缺少 entry point，请先调用 set_entry_point().")
    if not finish_point:
        raise GraphValidationError("缺少 finish point，请先调用 set_finish_point().")
    if entry_point not in nodes:
        raise GraphValidationError(f"entry point 不存在: {entry_point}")
    if finish_point not in nodes:
        raise GraphValidationError(f"finish point 不存在: {finish_point}")

    for source, destinations in edges.items():
        if source not in nodes:
            raise GraphValidationError(f"边起点不存在: {source}")
        for destination in destinations:
            if destination not in nodes:
                raise GraphValidationError(f"边终点不存在: {destination}")

    for source, destinations in conditional_edges.items():
        if source not in nodes:
            raise GraphValidationError(f"条件边起点不存在: {source}")
        for destination in destinations:
            if destination not in nodes:
                raise GraphValidationError(f"条件边终点不存在: {destination}")

    for node_name in nodes:
        if node_name == finish_point:
            continue
        has_static_edge = bool(edges.get(node_name))
        has_conditional_edge = node_name in conditional_edges
        if not has_static_edge and not has_conditional_edge:
            raise GraphValidationError(f"节点缺少出边: {node_name}")
