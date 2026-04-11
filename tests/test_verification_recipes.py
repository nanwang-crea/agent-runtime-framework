from agent_runtime_framework.workflow.recovery.verification import (
    get_verification_recipe,
    list_verification_recipe_payloads,
    workspace_write_verification_hint,
)


def test_post_write_recipe_registered():
    r = get_verification_recipe("post_write_workspace_path")
    assert r is not None
    assert r.required is True


def test_workspace_write_hint_maps_tools():
    hint = workspace_write_verification_hint("create_workspace_path")
    assert hint is not None
    assert hint["recipe_id"] == "post_write_workspace_path"


def test_list_recipes_non_empty():
    assert len(list_verification_recipe_payloads()) >= 3
