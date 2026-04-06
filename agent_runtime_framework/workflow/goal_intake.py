from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime_framework.workflow.goal_analysis import analyze_goal
from agent_runtime_framework.workflow.models import GoalEnvelope


def _memory_snapshot(application_context: Any | None) -> dict[str, Any]:
    if application_context is None:
        return {}
    session_memory = getattr(application_context, "session_memory", None)
    if session_memory is None or not hasattr(session_memory, "snapshot"):
        return {}
    snapshot = session_memory.snapshot()
    focused_resources = []
    for resource in getattr(snapshot, "focused_resources", []) or []:
        location = getattr(resource, "location", None)
        focused_resources.append(str(location or resource))
    return {
        "focused_resources": focused_resources,
        "last_summary": getattr(snapshot, "last_summary", None),
    }


def _workspace_snapshot(workspace_root: str | Path | None) -> dict[str, Any]:
    if workspace_root is None:
        return {}
    root = Path(workspace_root).expanduser().resolve()
    entries: list[str] = []
    if root.exists() and root.is_dir():
        entries = sorted(item.name for item in root.iterdir())[:20]
    return {
        "workspace_root": str(root),
        "top_level_entries": entries,
    }


def _constraints(application_context: Any | None) -> dict[str, Any]:
    config = dict(getattr(application_context, "config", {}) or {}) if application_context is not None else {}
    constraints: dict[str, Any] = {}
    if "max_dynamic_nodes" in config:
        constraints["max_dynamic_nodes"] = config["max_dynamic_nodes"]
    if "default_directory" in config:
        constraints["default_directory"] = config["default_directory"]
    return constraints


def build_goal_envelope(
    message: str,
    *,
    application_context: Any | None = None,
    workspace_root: str | Path | None = None,
    context: Any | None = None,
    goal_spec: Any | None = None,
) -> GoalEnvelope:
    analysis_context = context
    if analysis_context is None and application_context is not None:
        analysis_context = type("GoalAnalysisContext", (), {"application_context": application_context})()
    goal_spec = goal_spec or analyze_goal(message, context=analysis_context)
    normalized_goal = " ".join(message.split())
    target_hints: list[str] = []
    target_hint = str(goal_spec.metadata.get("target_hint") or "").strip()
    if target_hint and target_hint not in target_hints:
        target_hints.append(target_hint)
    success_criteria = ["produce a grounded response"]
    if goal_spec.requires_target_interpretation:
        success_criteria.append("resolve the intended target")
    if goal_spec.requires_search:
        success_criteria.append("collect targeted search evidence")
    if goal_spec.requires_read:
        success_criteria.append("collect target file evidence")
    if goal_spec.requires_verification:
        success_criteria.append("verify the final result")
    return GoalEnvelope(
        goal=message,
        normalized_goal=normalized_goal or message,
        intent=goal_spec.primary_intent,
        target_hints=target_hints,
        memory_snapshot=_memory_snapshot(application_context),
        workspace_snapshot=_workspace_snapshot(workspace_root),
        policy_context={},
        constraints=_constraints(application_context),
        success_criteria=success_criteria,
    )
