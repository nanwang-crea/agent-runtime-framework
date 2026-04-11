from __future__ import annotations

from agent_runtime_framework.capabilities.models import CapabilityMacro, CapabilitySpec
from agent_runtime_framework.capabilities.registry import CapabilityRegistry


def _spec(
    capability_id: str,
    description: str,
    *,
    intents: list[str],
    toolchains: list[list[str]],
    preconditions: list[str] | None = None,
    produces: list[str] | None = None,
    failure_signatures: list[str] | None = None,
    verification_recipe: list[str] | None = None,
    extension_policy: str = "reuse_only",
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        description=description,
        intents=intents,
        preconditions=list(preconditions or []),
        produces=list(produces or []),
        toolchains=toolchains,
        failure_signatures=list(failure_signatures or []),
        verification_recipe=list(verification_recipe or []),
        extension_policy=extension_policy,
    )


def build_default_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    defaults: list[CapabilitySpec] = [
        _spec(
            "resolve_target_in_workspace",
            "在仓库内解析歧义路径或目标文件。",
            intents=["target_explainer", "compound"],
            preconditions=["goal_interpreted"],
            produces=["resolved_target", "target_candidates"],
            toolchains=[["target_resolution"], ["interpret_target", "target_resolution"]],
            failure_signatures=["clarification", "ambiguous", "path_mismatch", "target"],
            verification_recipe=["resolve_target_smoke"],
        ),
        _spec(
            "search_workspace_content",
            "按语义或关键词搜索工作区内容。",
            intents=["compound", "repository_overview"],
            preconditions=["goal_interpreted"],
            produces=["evidence_items", "search_hits"],
            toolchains=[["content_search"], ["plan_search", "content_search"]],
            failure_signatures=["search_plan", "evidence", "missing_read"],
            verification_recipe=["search_hits_non_empty"],
        ),
        _spec(
            "search_workspace_symbols",
            "在工作区内做符号级或结构化定位（轻量搜索）。",
            intents=["compound"],
            preconditions=["goal_interpreted"],
            produces=["target_candidates", "evidence_items"],
            toolchains=[["content_search"], ["workspace_discovery"]],
            failure_signatures=["symbol", "definition"],
            verification_recipe=["search_hits_non_empty"],
        ),
        _spec(
            "read_workspace_evidence",
            "读取文件片段或结构化摘录作为证据。",
            intents=["file_read", "compound", "change_and_verify"],
            preconditions=["resolved_target"],
            produces=["chunks", "facts", "evidence_items"],
            toolchains=[["chunked_file_read"], ["plan_read", "chunked_file_read"]],
            failure_signatures=["read_plan", "grounded", "chunk", "missing_read"],
            verification_recipe=["read_nonempty_excerpt"],
        ),
        _spec(
            "move_or_rename_path",
            "移动或重命名工作区内路径。",
            intents=["change_and_verify", "dangerous_change"],
            preconditions=["resolved_target"],
            produces=["modified_paths", "filesystem_changes"],
            toolchains=[["move_path", "tool_call"]],
            failure_signatures=["move_workspace_path", "sandbox_policy", "path_outside_workspace"],
            verification_recipe=["post_write_workspace_path"],
        ),
        _spec(
            "create_workspace_path",
            "在工作区创建新文件或目录（含初始内容）。",
            intents=["change_and_verify"],
            preconditions=["resolved_target"],
            produces=["modified_paths", "filesystem_changes"],
            toolchains=[["create_path", "tool_call"]],
            failure_signatures=["create_workspace_path", "sandbox_policy", "path_outside_workspace", "tool_validation"],
            verification_recipe=["post_write_workspace_path"],
        ),
        _spec(
            "delete_workspace_path",
            "删除工作区内路径并确认删除结果。",
            intents=["dangerous_change"],
            preconditions=["resolved_target"],
            produces=["modified_paths", "filesystem_changes"],
            toolchains=[["delete_path", "tool_call"]],
            failure_signatures=["delete_workspace_path", "sandbox_policy", "path_outside_workspace"],
            verification_recipe=["post_write_workspace_path"],
        ),
        _spec(
            "edit_workspace_file",
            "编辑、追加或打补丁修改工作区文件。",
            intents=["change_and_verify"],
            preconditions=["resolved_target", "evidence_items"],
            produces=["modified_paths", "write_intent", "verification_target"],
            toolchains=[["write_file", "append_text", "apply_patch", "tool_call"]],
            failure_signatures=[
                "tool_validation",
                "tool_execution",
                "edit_workspace_text",
                "apply_text_patch",
                "append_workspace_text",
            ],
            verification_recipe=["post_write_workspace_path"],
        ),
        _spec(
            "run_workspace_verification",
            "运行测试或检查以验证工作区变更。",
            intents=["change_and_verify"],
            preconditions=["modified_paths"],
            produces=["verification_result", "verification_events"],
            toolchains=[["verification", "verification_step"]],
            failure_signatures=["verification_missing", "verification_failed", "pytest"],
            verification_recipe=["rerun_workspace_tests"],
        ),
        _spec(
            "inspect_test_failure",
            "根据测试失败输出定位问题并收集证据。",
            intents=["change_and_verify", "compound"],
            preconditions=["verification_result"],
            produces=["failure_evidence", "evidence_items", "chunks"],
            toolchains=[["verification_step", "chunked_file_read"], ["tool_call"]],
            failure_signatures=["test", "pytest", "failed", "stderr"],
            verification_recipe=["rerun_workspace_tests"],
        ),
    ]
    for spec in defaults:
        registry.register(spec)
    for recipe in default_capability_macros():
        registry.register_recipe(recipe)
    return registry


def default_capability_macros() -> list[CapabilityMacro]:
    return [
        CapabilityMacro(
            recipe_id="resolve_then_read_target",
            description="先解析目标再读取证据。",
            intent_scope=["file_read", "target_explainer", "compound"],
            entry_conditions=["target_may_be_ambiguous"],
            required_capabilities=["resolve_target_in_workspace", "read_workspace_evidence"],
            exit_conditions=["chunks_available"],
            verification_strategy="read_nonempty_excerpt",
        ),
        CapabilityMacro(
            recipe_id="search_then_read_evidence",
            description="先搜索定位再读取证据。",
            intent_scope=["compound", "repository_overview"],
            entry_conditions=["target_unknown"],
            required_capabilities=["search_workspace_content", "read_workspace_evidence"],
            optional_capabilities=["resolve_target_in_workspace"],
            exit_conditions=["grounded_evidence_collected"],
            verification_strategy="search_hits_non_empty",
        ),
        CapabilityMacro(
            recipe_id="resolve_then_create_path",
            description="解析目标路径后创建文件或目录并做写后校验。",
            intent_scope=["change_and_verify"],
            entry_conditions=["new_path_or_file_requested"],
            required_capabilities=[
                "resolve_target_in_workspace",
                "create_workspace_path",
                "run_workspace_verification",
            ],
            optional_capabilities=["read_workspace_evidence"],
            exit_conditions=["filesystem_changes", "verification_result"],
            fallback_recipes=["locate_inspect_edit_verify"],
            verification_strategy="post_write_workspace_path",
        ),
        CapabilityMacro(
            recipe_id="locate_inspect_edit_verify",
            description="先定位并检查目标，再修改并做验证。",
            intent_scope=["change_and_verify"],
            entry_conditions=["workspace_change_requested"],
            required_capabilities=[
                "resolve_target_in_workspace",
                "read_workspace_evidence",
                "edit_workspace_file",
                "run_workspace_verification",
            ],
            exit_conditions=["verification_result"],
            fallback_recipes=["inspect_patch_verify_python", "resolve_then_create_path"],
            verification_strategy="post_write_workspace_path",
        ),
        CapabilityMacro(
            recipe_id="inspect_patch_verify_python",
            description="针对 Python 改动进行检查、补丁修改和测试验证。",
            intent_scope=["change_and_verify"],
            entry_conditions=["python_workspace_change_requested"],
            required_capabilities=["read_workspace_evidence", "edit_workspace_file", "run_workspace_verification"],
            optional_capabilities=["inspect_test_failure"],
            exit_conditions=["verification_result"],
            fallback_recipes=["locate_inspect_edit_verify"],
            verification_strategy="rerun_workspace_tests",
        ),
        CapabilityMacro(
            recipe_id="resolve_then_move_or_rename",
            description="解析路径后执行移动或重命名，并做结果确认。",
            intent_scope=["dangerous_change", "change_and_verify"],
            entry_conditions=["path_change_requested", "rename_or_move_requested"],
            required_capabilities=["resolve_target_in_workspace", "move_or_rename_path", "run_workspace_verification"],
            exit_conditions=["filesystem_changes", "verification_result"],
            fallback_recipes=["resolve_then_delete_path"],
            verification_strategy="post_write_workspace_path",
        ),
        CapabilityMacro(
            recipe_id="resolve_then_delete_path",
            description="先解析目标路径，再执行删除并校验结果。",
            intent_scope=["dangerous_change"],
            entry_conditions=["delete_requested"],
            required_capabilities=["resolve_target_in_workspace", "delete_workspace_path", "run_workspace_verification"],
            exit_conditions=["filesystem_changes", "verification_result"],
            fallback_recipes=["resolve_then_move_or_rename"],
            verification_strategy="post_write_workspace_path",
        ),
    ]
