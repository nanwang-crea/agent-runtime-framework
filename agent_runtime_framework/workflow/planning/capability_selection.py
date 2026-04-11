from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime_framework.capabilities.models import CapabilityMacro
from agent_runtime_framework.capabilities.registry import resolve_capability_registry
from agent_runtime_framework.workflow.state.models import AgentGraphState, GoalEnvelope


@dataclass(slots=True)
class CapabilityPlanSelection:
    planner_summary: str
    recipe_id: str = ""
    capability_ids: list[str] = field(default_factory=list)
    expansion_hints: dict[str, dict[str, Any]] = field(default_factory=dict)
    unresolved_preconditions: list[str] = field(default_factory=list)
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "planner_summary": self.planner_summary,
            "recipe_id": self.recipe_id,
            "capability_ids": list(self.capability_ids),
            "expansion_hints": {key: dict(value) for key, value in self.expansion_hints.items()},
            "unresolved_preconditions": list(self.unresolved_preconditions),
            "rationale": self.rationale,
            "metadata": dict(self.metadata),
        }


def _services_from_context(context: Any | None) -> dict[str, Any]:
    if context is None:
        return {}
    if isinstance(context, dict):
        return dict(context.get("services") or {})
    return dict(getattr(context, "services", {}) or {})


def _latest_judge_payload(graph_state: AgentGraphState) -> dict[str, Any]:
    if not graph_state.judge_history:
        return {}
    latest = graph_state.judge_history[-1]
    if hasattr(latest, "as_payload"):
        return dict(latest.as_payload())
    if isinstance(latest, dict):
        return dict(latest)
    return {}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _goal_text(goal_envelope: GoalEnvelope) -> str:
    return f"{goal_envelope.goal} {goal_envelope.normalized_goal}".lower()


def _suggests_delete(goal_text: str) -> bool:
    return any(
        token in goal_text
        for token in ("删除", "remove", "delete", "unlink", "rm ", " rm", " rm ")
    )


def _suggests_move_rename(goal_text: str) -> bool:
    return any(
        token in goal_text
        for token in ("重命名", "移动", "rename", "move", " mv", "mv ", "mv\t")
    )


def _suggests_new_path_creation(goal_text: str) -> bool:
    return any(
        token in goal_text
        for token in (
            "创建",
            "新建",
            "mkdir",
            "touch",
            "new file",
            "新文件",
            "空文件",
            "目录",
            "folder",
            "add file",
        )
    )


def _recent_failure_text(graph_state: AgentGraphState) -> str:
    if not graph_state.failure_history:
        return ""
    latest = dict(graph_state.failure_history[-1] or {})
    fd = dict(latest.get("failure_diagnosis") or {})
    return " ".join(
        [
            str(latest.get("reason") or ""),
            str(fd.get("category") or ""),
            str(fd.get("subcategory") or ""),
            str(fd.get("summary") or ""),
            str(fd.get("blocking_issue") or ""),
        ]
    ).lower()


def _find_recipe(registry: Any, recipe_id: str) -> CapabilityMacro | None:
    token = str(recipe_id or "").strip()
    if not token:
        return None
    return registry.get_recipe(token) if hasattr(registry, "get_recipe") else None


def _infer_recipe_id(goal_envelope: GoalEnvelope, graph_state: AgentGraphState, planner_payload: dict[str, Any], registry: Any) -> str:
    judge_payload = _latest_judge_payload(graph_state)
    blocked = {str(item).strip() for item in judge_payload.get("blocked_recipe_ids", []) or [] if str(item).strip()}

    def _allowed(recipe_id: str) -> bool:
        token = str(recipe_id).strip()
        return bool(token) and token not in blocked and getattr(registry, "has_recipe", lambda _value: False)(token)

    requested = str(planner_payload.get("selected_recipe_id") or planner_payload.get("recipe_id") or "").strip()
    if _allowed(requested):
        return requested
    for candidate in judge_payload.get("preferred_recipe_ids", []) or []:
        token = str(candidate).strip()
        if _allowed(token):
            return token
    goal_text = _goal_text(goal_envelope)
    failure_text = _recent_failure_text(graph_state)
    fallback_candidates: list[str]
    if goal_envelope.intent == "dangerous_change":
        if _suggests_delete(goal_text):
            fallback_candidates = ["resolve_then_delete_path", "resolve_then_move_or_rename"]
        elif _suggests_move_rename(goal_text):
            fallback_candidates = ["resolve_then_move_or_rename", "resolve_then_delete_path"]
        else:
            fallback_candidates = ["resolve_then_move_or_rename", "resolve_then_delete_path"]
    elif goal_envelope.intent == "change_and_verify":
        if _suggests_new_path_creation(goal_text) and not _suggests_delete(goal_text):
            fallback_candidates = ["resolve_then_create_path", "locate_inspect_edit_verify", "inspect_patch_verify_python"]
        elif any(keyword in f"{goal_text} {failure_text}" for keyword in ("pytest", "test", "python", ".py")):
            fallback_candidates = ["inspect_patch_verify_python", "locate_inspect_edit_verify"]
        else:
            fallback_candidates = ["locate_inspect_edit_verify", "inspect_patch_verify_python", "resolve_then_create_path"]
    elif goal_envelope.intent in {"repository_overview", "compound"}:
        fallback_candidates = ["search_then_read_evidence", "resolve_then_read_target"]
    else:
        fallback_candidates = ["resolve_then_read_target", "search_then_read_evidence"]
    for candidate in fallback_candidates:
        if _allowed(candidate):
            return candidate
    return ""


def _optional_capability_allowed(
    capability_id: str,
    goal_envelope: GoalEnvelope,
    graph_state: AgentGraphState,
    recipe: CapabilityMacro | None,
) -> bool:
    goal_text = _goal_text(goal_envelope)
    failure_text = _recent_failure_text(graph_state)
    if capability_id == "resolve_target_in_workspace":
        working_memory = graph_state.memory_state.working_memory
        return not bool(str(working_memory.active_target or "").strip() and working_memory.confirmed_targets)
    if capability_id == "inspect_test_failure":
        return any(keyword in f"{goal_text} {failure_text}" for keyword in ("pytest", "test", "failed", "stderr"))
    if capability_id == "read_workspace_evidence" and recipe is not None and recipe.recipe_id == "resolve_then_create_path":
        return bool(failure_text) and any(
            keyword in failure_text for keyword in ("ambiguous", "clarification", "unknown", "missing", "path")
        )
    return True


def _resolve_capability_ids(
    recipe: CapabilityMacro | None,
    goal_envelope: GoalEnvelope,
    graph_state: AgentGraphState,
    planner_payload: dict[str, Any],
    registry: Any,
) -> list[str]:
    requested = [str(item).strip() for item in planner_payload.get("selected_capability_ids", []) or [] if str(item).strip()]
    valid_requested = [item for item in requested if registry.has(item)]
    if valid_requested:
        return _dedupe(valid_requested)
    capability_ids: list[str] = []
    if recipe is not None:
        capability_ids.extend(recipe.required_capabilities)
        for capability_id in recipe.optional_capabilities:
            if _optional_capability_allowed(capability_id, goal_envelope, graph_state, recipe):
                capability_ids.append(capability_id)
    judge_payload = _latest_judge_payload(graph_state)
    for capability_id in judge_payload.get("preferred_capability_ids", []) or []:
        token = str(capability_id).strip()
        if token and registry.has(token):
            capability_ids.append(token)
    for capability_id in judge_payload.get("must_cover_capabilities", []) or []:
        token = str(capability_id).strip()
        if token and registry.has(token):
            capability_ids.append(token)
    if not capability_ids:
        gt = _goal_text(goal_envelope)
        if goal_envelope.intent == "change_and_verify":
            if _suggests_new_path_creation(gt) and not _suggests_delete(gt):
                capability_ids = ["resolve_target_in_workspace", "create_workspace_path", "run_workspace_verification"]
            else:
                capability_ids = ["read_workspace_evidence", "edit_workspace_file", "run_workspace_verification"]
        elif goal_envelope.intent == "dangerous_change":
            if _suggests_delete(gt):
                capability_ids = ["resolve_target_in_workspace", "delete_workspace_path", "run_workspace_verification"]
            elif _suggests_move_rename(gt):
                capability_ids = ["resolve_target_in_workspace", "move_or_rename_path", "run_workspace_verification"]
            else:
                capability_ids = ["resolve_target_in_workspace", "move_or_rename_path", "run_workspace_verification"]
        elif goal_envelope.intent in {"compound", "repository_overview"}:
            capability_ids = ["search_workspace_content", "read_workspace_evidence"]
        else:
            capability_ids = ["resolve_target_in_workspace", "read_workspace_evidence"]
    if bool(judge_payload.get("verification_required")) and "run_workspace_verification" not in capability_ids and registry.has("run_workspace_verification"):
        capability_ids.append("run_workspace_verification")
    return _dedupe([item for item in capability_ids if registry.has(item)])


def _selection_preconditions(capability_ids: list[str], registry: Any) -> list[str]:
    preconditions: list[str] = []
    for capability_id in capability_ids:
        spec = registry.get(capability_id)
        if spec is None:
            continue
        preconditions.extend(spec.preconditions)
    return _dedupe(preconditions)


def select_capability_plan(
    goal_envelope: GoalEnvelope,
    graph_state: AgentGraphState,
    context: Any | None = None,
    planner_payload: dict[str, Any] | None = None,
) -> CapabilityPlanSelection:
    planner_payload = dict(planner_payload or {})
    registry = resolve_capability_registry(_services_from_context(context))
    recipe_id = _infer_recipe_id(goal_envelope, graph_state, planner_payload, registry)
    recipe = _find_recipe(registry, recipe_id)
    capability_ids = _resolve_capability_ids(recipe, goal_envelope, graph_state, planner_payload, registry)
    planner_summary = str(planner_payload.get("planner_summary") or "").strip()
    if not planner_summary:
        if recipe is not None:
            planner_summary = f"Select recipe {recipe.recipe_id} for intent {goal_envelope.intent}"
        else:
            planner_summary = f"Select capability chain for intent {goal_envelope.intent}"
    expansion_hints = {
        str(key).strip(): dict(value)
        for key, value in dict(planner_payload.get("expansion_hints") or {}).items()
        if str(key).strip() and isinstance(value, dict)
    }
    unresolved_preconditions = _selection_preconditions(capability_ids, registry)
    return CapabilityPlanSelection(
        planner_summary=planner_summary,
        recipe_id=recipe.recipe_id if recipe is not None else "",
        capability_ids=capability_ids,
        expansion_hints=expansion_hints,
        unresolved_preconditions=unresolved_preconditions,
        rationale=str(planner_payload.get("rationale") or planner_payload.get("selection_reason") or "").strip(),
        metadata={
            "selected_intent": goal_envelope.intent,
            "requested_recipe_id": str(planner_payload.get("selected_recipe_id") or planner_payload.get("recipe_id") or "").strip() or None,
            "preferred_recipe_ids": [str(item).strip() for item in _latest_judge_payload(graph_state).get("preferred_recipe_ids", []) or [] if str(item).strip()],
        },
    )

