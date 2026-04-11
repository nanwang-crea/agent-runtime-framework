from __future__ import annotations

from agent_runtime_framework.capabilities.models import CapabilityMacro, CapabilitySpec
from agent_runtime_framework.capabilities.registry import CapabilityRegistry


def _spec(
    capability_id: str,
    description: str,
    *,
    intents: list[str],
    toolchains: list[list[str]],
    prerequisites: list[str] | None = None,
    failure_signatures: list[str] | None = None,
    verification_recipe: list[str] | None = None,
    extension_policy: str = "reuse_only",
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        description=description,
        intents=intents,
        toolchains=toolchains,
        prerequisites=list(prerequisites or []),
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
            toolchains=[["target_resolution"], ["interpret_target", "target_resolution"]],
            failure_signatures=["clarification", "ambiguous", "path_mismatch", "target"],
            verification_recipe=["resolve_target_smoke"],
        ),
        _spec(
            "search_workspace_content",
            "按语义或关键词搜索工作区内容。",
            intents=["compound", "repository_overview"],
            toolchains=[["content_search"], ["plan_search", "content_search"]],
            failure_signatures=["search_plan", "evidence", "missing_read"],
            verification_recipe=["search_hits_non_empty"],
        ),
        _spec(
            "search_workspace_symbols",
            "在工作区内做符号级或结构化定位（轻量搜索）。",
            intents=["compound"],
            toolchains=[["content_search"], ["workspace_discovery"]],
            failure_signatures=["symbol", "definition"],
            verification_recipe=["search_hits_non_empty"],
        ),
        _spec(
            "read_workspace_evidence",
            "读取文件片段或结构化摘录作为证据。",
            intents=["file_read", "compound", "change_and_verify"],
            toolchains=[["chunked_file_read"], ["plan_read", "chunked_file_read"]],
            failure_signatures=["read_plan", "grounded", "chunk", "missing_read"],
            verification_recipe=["read_nonempty_excerpt"],
        ),
        _spec(
            "move_or_rename_path",
            "移动或重命名工作区内路径。",
            intents=["change_and_verify", "dangerous_change"],
            toolchains=[["move_path", "tool_call"]],
            failure_signatures=["move_workspace_path", "sandbox_policy", "path_outside_workspace"],
            verification_recipe=["post_write_workspace_path"],
        ),
        _spec(
            "edit_workspace_file",
            "编辑、追加或打补丁修改工作区文件。",
            intents=["change_and_verify"],
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
            toolchains=[["verification", "verification_step"]],
            failure_signatures=["verification_missing", "verification_failed", "pytest"],
            verification_recipe=["rerun_workspace_tests"],
        ),
        _spec(
            "inspect_test_failure",
            "根据测试失败输出定位问题并收集证据。",
            intents=["change_and_verify", "compound"],
            toolchains=[["verification_step", "chunked_file_read"], ["tool_call"]],
            failure_signatures=["test", "pytest", "failed", "stderr"],
            verification_recipe=["rerun_workspace_tests"],
        ),
    ]
    for spec in defaults:
        registry.register(spec)
    return registry


def default_capability_macros() -> list[CapabilityMacro]:
    return [
        CapabilityMacro(
            macro_id="resolve_target_then_read",
            description="先解析目标再读取证据。",
            capability_chain=["resolve_target_in_workspace", "read_workspace_evidence"],
        ),
        CapabilityMacro(
            macro_id="inspect_and_patch_file",
            description="读片段、修改文件并做写后校验。",
            capability_chain=["read_workspace_evidence", "edit_workspace_file", "run_workspace_verification"],
        ),
        CapabilityMacro(
            macro_id="repair_and_verify_python_test",
            description="修复后重跑 Python 测试验证。",
            capability_chain=["edit_workspace_file", "run_workspace_verification", "inspect_test_failure"],
        ),
    ]
