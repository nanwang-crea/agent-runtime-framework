from __future__ import annotations

from typing import Any

from agent_runtime_framework.capabilities.registry import resolve_capability_registry
from agent_runtime_framework.workflow.planning.capability_selection import CapabilityPlanSelection
from agent_runtime_framework.workflow.state.models import AgentGraphState, GoalEnvelope, PlannedNode, PlannedSubgraph, WorkflowEdge


def _services_from_context(context: Any | None) -> dict[str, Any]:
    if context is None:
        return {}
    if isinstance(context, dict):
        return dict(context.get("services") or {})
    return dict(getattr(context, "services", {}) or {})


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        token = str(value).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _write_node_for_goal(goal_envelope: GoalEnvelope, hint: dict[str, Any]) -> str:
    preferred_node_type = str(hint.get("preferred_node_type") or hint.get("node_type") or "").strip()
    if preferred_node_type in {"apply_patch", "write_file", "append_text"}:
        return preferred_node_type
    goal_text = f"{goal_envelope.goal} {goal_envelope.normalized_goal}".lower()
    if any(keyword in goal_text for keyword in ("append", "追加")):
        return "append_text"
    if any(keyword in goal_text for keyword in ("create", "new file", "rewrite", "replace all", "覆盖", "重写")):
        return "write_file"
    return "apply_patch"


def _default_toolchain(capability_id: str, goal_envelope: GoalEnvelope, hint: dict[str, Any]) -> list[str]:
    if capability_id == "resolve_target_in_workspace":
        return ["interpret_target", "target_resolution"]
    if capability_id in {"search_workspace_content", "search_workspace_symbols"}:
        return ["plan_search", "content_search"]
    if capability_id == "read_workspace_evidence":
        return ["plan_read", "chunked_file_read"]
    if capability_id == "move_or_rename_path":
        return ["move_path"]
    if capability_id == "delete_workspace_path":
        return ["delete_path"]
    if capability_id == "edit_workspace_file":
        return [_write_node_for_goal(goal_envelope, hint)]
    if capability_id == "run_workspace_verification":
        return ["verification"]
    if capability_id == "inspect_test_failure":
        return ["verification_step", "chunked_file_read"]
    return ["tool_call"]


def _select_toolchain(spec: Any, capability_id: str, goal_envelope: GoalEnvelope, hint: dict[str, Any]) -> list[str]:
    requested = [str(item).strip() for item in hint.get("preferred_toolchain", []) or [] if str(item).strip()]
    if requested and requested in [list(chain) for chain in getattr(spec, "toolchains", []) or []]:
        return requested
    fallback = _default_toolchain(capability_id, goal_envelope, hint)
    if requested and fallback == ["tool_call"] and requested:
        return requested
    return fallback


def _node_success_criteria(node_type: str, spec: Any) -> list[str]:
    if getattr(spec, "produces", None):
        return [f"produce {item}" for item in spec.produces[:2]]
    defaults = {
        "interpret_target": ["capture target constraints"],
        "plan_search": ["define search plan"],
        "content_search": ["collect search evidence"],
        "plan_read": ["define read plan"],
        "chunked_file_read": ["collect grounded evidence"],
        "target_resolution": ["resolve target"],
        "apply_patch": ["modify target file"],
        "write_file": ["write target file"],
        "append_text": ["append requested content"],
        "move_path": ["move target path"],
        "delete_path": ["delete target path"],
        "verification": ["produce verification result"],
        "verification_step": ["collect verification detail"],
        "tool_call": ["execute requested tool action"],
    }
    return list(defaults.get(node_type, [f"complete {node_type}"]))


def _base_node_inputs(capability_id: str, recipe_id: str, hint: dict[str, Any], spec: Any, node_type: str) -> dict[str, Any]:
    inputs = dict(hint.get("node_inputs") or hint.get("inputs") or {})
    metadata = dict(inputs)
    metadata.setdefault("capability_id", capability_id)
    if recipe_id:
        metadata.setdefault("recipe_id", recipe_id)
    if node_type == "verification":
        recipe_ids = list(getattr(spec, "verification_recipe", []) or [])
        if recipe_ids:
            metadata.setdefault("verification_recipe_id", recipe_ids[0])
        metadata.setdefault("verification_type", "post_change")
    return metadata


def expand_recipe_selection(
    goal_envelope: GoalEnvelope,
    graph_state: AgentGraphState,
    selection: CapabilityPlanSelection,
    *,
    iteration: int,
    context: Any | None = None,
) -> PlannedSubgraph:
    registry = resolve_capability_registry(_services_from_context(context))
    nodes: list[PlannedNode] = []
    previous_node_id = ""
    emitted_node_ids: set[str] = set()
    toolchain_by_capability: dict[str, list[str]] = {}

    for capability_id in selection.capability_ids:
        spec = registry.get(capability_id)
        if spec is None:
            continue
        hint = dict(selection.expansion_hints.get(capability_id) or {})
        toolchain = _select_toolchain(spec, capability_id, goal_envelope, hint)
        toolchain_by_capability[capability_id] = list(toolchain)
        capability_start_id = previous_node_id
        for node_type in toolchain:
            base_id = f"{node_type}_{iteration}"
            node_id = base_id
            suffix = 0
            while node_id in emitted_node_ids:
                suffix += 1
                node_id = f"{base_id}_{suffix}"
            emitted_node_ids.add(node_id)
            depends_on = [previous_node_id] if previous_node_id else []
            if capability_start_id and node_type == toolchain[0]:
                depends_on = [capability_start_id]
            node = PlannedNode(
                node_id=node_id,
                node_type=node_type,
                reason=str(hint.get("reason") or spec.description or f"Execute {capability_id}").strip(),
                inputs=_base_node_inputs(capability_id, selection.recipe_id, hint, spec, node_type),
                depends_on=[dependency for dependency in depends_on if dependency],
                success_criteria=_node_success_criteria(node_type, spec),
                requires_approval=bool(hint.get("requires_approval", False)),
            )
            nodes.append(node)
            previous_node_id = node_id

    edges = [WorkflowEdge(source=dependency, target=node.node_id) for node in nodes for dependency in node.depends_on]
    planner_summary = selection.planner_summary or f"Expand recipe {selection.recipe_id or 'capability_chain'}"
    return PlannedSubgraph(
        iteration=iteration,
        planner_summary=planner_summary,
        nodes=nodes,
        edges=edges,
        metadata={
            "planner": "recipe_expansion_v1",
            "recipe_id": selection.recipe_id or None,
            "capability_ids": list(selection.capability_ids),
            "unresolved_preconditions": _dedupe(selection.unresolved_preconditions),
            "toolchain_by_capability": toolchain_by_capability,
        },
    )

