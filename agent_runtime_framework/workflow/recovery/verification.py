from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from agent_runtime_framework.workflow.recovery.models import normalize_recovery_mode


@dataclass(slots=True)
class VerificationRecipe:
    recipe_id: str
    steps: list[str] = field(default_factory=list)
    required: bool = True
    on_failure_recovery_mode: str = "collect_more_evidence"

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["on_failure_recovery_mode"] = normalize_recovery_mode(
            payload.get("on_failure_recovery_mode"),
            default="collect_more_evidence",
        )
        return payload


_VERIFICATION_RECIPES: dict[str, VerificationRecipe] = {
    "post_write_workspace_path": VerificationRecipe(
        recipe_id="post_write_workspace_path",
        steps=["collect_post_write_events", "assert_latest_post_write_passed"],
        required=True,
        on_failure_recovery_mode="repair_arguments",
    ),
    "rerun_workspace_tests": VerificationRecipe(
        recipe_id="rerun_workspace_tests",
        steps=["surface_tool_verification_events", "assert_no_failed_verification"],
        required=True,
        on_failure_recovery_mode="collect_more_evidence",
    ),
    "read_nonempty_excerpt": VerificationRecipe(
        recipe_id="read_nonempty_excerpt",
        steps=["assert_chunks_or_evidence_present"],
        required=False,
        on_failure_recovery_mode="collect_more_evidence",
    ),
    "search_hits_non_empty": VerificationRecipe(
        recipe_id="search_hits_non_empty",
        steps=["assert_search_artifacts_present"],
        required=False,
        on_failure_recovery_mode="collect_more_evidence",
    ),
    "resolve_target_smoke": VerificationRecipe(
        recipe_id="resolve_target_smoke",
        steps=["assert_resolved_target_or_clarification"],
        required=False,
        on_failure_recovery_mode="request_clarification",
    ),
    "extension_smoke_default": VerificationRecipe(
        recipe_id="extension_smoke_default",
        steps=["record_extension_audit", "noop_smoke_ok"],
        required=True,
        on_failure_recovery_mode="handoff_to_human",
    ),
}


def get_verification_recipe(recipe_id: str) -> VerificationRecipe | None:
    return _VERIFICATION_RECIPES.get(str(recipe_id or "").strip())


def list_verification_recipe_payloads() -> list[dict[str, Any]]:
    return [recipe.as_payload() for recipe in _VERIFICATION_RECIPES.values()]


_TOOL_TO_PRIMARY_RECIPE: dict[str, str] = {
    "create_workspace_path": "post_write_workspace_path",
    "move_workspace_path": "post_write_workspace_path",
    "delete_workspace_path": "post_write_workspace_path",
    "apply_text_patch": "post_write_workspace_path",
    "edit_workspace_text": "post_write_workspace_path",
    "append_workspace_text": "post_write_workspace_path",
}


def workspace_write_verification_hint(tool_name: str) -> dict[str, Any] | None:
    recipe_id = _TOOL_TO_PRIMARY_RECIPE.get(str(tool_name or "").strip())
    if not recipe_id:
        return None
    recipe = get_verification_recipe(recipe_id)
    if recipe is None:
        return None
    return {
        "recipe_id": recipe.recipe_id,
        "required": recipe.required,
        "steps": list(recipe.steps),
        "on_failure_recovery_mode": recipe.on_failure_recovery_mode,
    }
