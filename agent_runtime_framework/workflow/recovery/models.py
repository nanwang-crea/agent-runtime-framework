from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


RECOVERY_MODES = frozenset(
    {
        "retry_same_action",
        "repair_arguments",
        "switch_tool",
        "collect_more_evidence",
        "request_clarification",
        "run_verification",
        "repair_environment",
        "compose_capability",
        "extend_capability",
        "handoff_to_human",
    }
)

DEFAULT_RECOVERY_MODE = "collect_more_evidence"


@dataclass(slots=True)
class FailureDiagnosis:
    category: str
    subcategory: str | None = None
    summary: str = ""
    blocking_issue: str = ""
    recoverable: bool = True
    suggested_recovery_mode: str = DEFAULT_RECOVERY_MODE
    missing_capability: str | None = None
    suggested_capabilities: list[str] = field(default_factory=list)
    suggested_recipes: list[str] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["suggested_recovery_mode"] = normalize_recovery_mode(
            payload.get("suggested_recovery_mode"),
            default=DEFAULT_RECOVERY_MODE,
        )
        return payload


def normalize_recovery_mode(value: Any, *, default: str = DEFAULT_RECOVERY_MODE) -> str:
    normalized_default = str(default or DEFAULT_RECOVERY_MODE).strip() or DEFAULT_RECOVERY_MODE
    candidate = str(value or "").strip()
    if candidate in RECOVERY_MODES:
        return candidate
    if normalized_default in RECOVERY_MODES:
        return normalized_default
    return DEFAULT_RECOVERY_MODE


def tool_validation_failure(*, subcategory: str, summary: str, blocking_issue: str) -> FailureDiagnosis:
    return FailureDiagnosis(
        category="tool_validation",
        subcategory=subcategory,
        summary=summary,
        blocking_issue=blocking_issue,
        recoverable=True,
        suggested_recovery_mode="repair_arguments",
    )


def tool_execution_failure(
    *,
    summary: str,
    blocking_issue: str,
    recoverable: bool = True,
    subcategory: str | None = None,
    suggested_recovery_mode: str = "retry_same_action",
    suggested_tools: list[str] | None = None,
    suggested_capabilities: list[str] | None = None,
    missing_capability: str | None = None,
) -> FailureDiagnosis:
    return FailureDiagnosis(
        category="tool_execution",
        subcategory=subcategory,
        summary=summary,
        blocking_issue=blocking_issue,
        recoverable=recoverable,
        suggested_recovery_mode=normalize_recovery_mode(
            suggested_recovery_mode,
            default="retry_same_action" if recoverable else "handoff_to_human",
        ),
        suggested_tools=list(suggested_tools or []),
        suggested_capabilities=list(suggested_capabilities or []),
        missing_capability=missing_capability,
    )


def judge_failure_diagnosis(
    *,
    summary: str,
    blocking_issue: str,
    primary_gap: str | None = None,
    capability_gap: str | None = None,
    preferred_capability_ids: list[str] | None = None,
    preferred_recipe_ids: list[str] | None = None,
    verification_required: bool = False,
    human_handoff_required: bool = False,
    recommended_recovery_mode: str | None = None,
) -> FailureDiagnosis:
    gap = str(primary_gap or "").strip()
    if human_handoff_required:
        category = "human_handoff"
        default_mode = "handoff_to_human"
    elif verification_required or gap == "verification_missing":
        category = "verification_gap"
        default_mode = "run_verification"
    elif gap == "clarification_missing":
        category = "clarification_required"
        default_mode = "request_clarification"
    elif gap == "conflicting_evidence":
        category = "evidence_gap"
        default_mode = "collect_more_evidence"
    else:
        category = "planning_gap"
        default_mode = "collect_more_evidence"
    suggested_capabilities = [str(item).strip() for item in preferred_capability_ids or [] if str(item).strip()]
    suggested_recipes = [str(item).strip() for item in preferred_recipe_ids or [] if str(item).strip()]
    missing_capability = str(capability_gap or "").strip() or None
    if verification_required and "run_workspace_verification" not in suggested_capabilities:
        suggested_capabilities.append("run_workspace_verification")
    if gap == "conflicting_evidence":
        for capability_id in ("resolve_target_in_workspace", "read_workspace_evidence"):
            if capability_id not in suggested_capabilities:
                suggested_capabilities.append(capability_id)
        for recipe_id in ("resolve_then_read_target", "search_then_read_evidence"):
            if recipe_id not in suggested_recipes:
                suggested_recipes.append(recipe_id)
    if gap == "clarification_missing" and not missing_capability:
        missing_capability = "resolve_target_in_workspace"
        if "resolve_target_in_workspace" not in suggested_capabilities:
            suggested_capabilities.append("resolve_target_in_workspace")
    if gap == "verification_missing" and not missing_capability:
        missing_capability = "run_workspace_verification"
    return FailureDiagnosis(
        category=category,
        subcategory=gap or None,
        summary=summary,
        blocking_issue=blocking_issue,
        recoverable=not human_handoff_required,
        suggested_recovery_mode=normalize_recovery_mode(recommended_recovery_mode, default=default_mode),
        missing_capability=missing_capability,
        suggested_capabilities=suggested_capabilities,
        suggested_recipes=suggested_recipes,
    )


def execution_failure_diagnosis(summary: str, *, blocking_issue: str = "") -> FailureDiagnosis:
    return FailureDiagnosis(
        category="execution_failure",
        summary=summary,
        blocking_issue=blocking_issue or summary,
        recoverable=True,
        suggested_recovery_mode="collect_more_evidence",
    )


__all__ = [
    "DEFAULT_RECOVERY_MODE",
    "FailureDiagnosis",
    "RECOVERY_MODES",
    "execution_failure_diagnosis",
    "judge_failure_diagnosis",
    "normalize_recovery_mode",
    "tool_execution_failure",
    "tool_validation_failure",
]
