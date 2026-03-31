from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolArgumentRule:
    required: tuple[str, ...] = ()
    requires_path_context: bool = False
    path_recovery: tuple[str, ...] = ()


TOOL_ARGUMENT_RULES: dict[str, ToolArgumentRule] = {
    "run_shell_command": ToolArgumentRule(required=("command",)),
    "read_workspace_text": ToolArgumentRule(requires_path_context=True, path_recovery=("workspace_root", "intent_target", "last_focus")),
    "read_workspace_excerpt": ToolArgumentRule(requires_path_context=True, path_recovery=("workspace_root", "intent_target", "last_focus")),
    "summarize_workspace_text": ToolArgumentRule(requires_path_context=True, path_recovery=("workspace_root", "intent_target", "last_focus")),
    "inspect_workspace_path": ToolArgumentRule(requires_path_context=True, path_recovery=("workspace_root", "intent_target", "last_focus")),
    "list_workspace_directory": ToolArgumentRule(requires_path_context=True, path_recovery=("workspace_root", "intent_target", "last_focus")),
    "extract_workspace_outline": ToolArgumentRule(requires_path_context=True, path_recovery=("intent_target", "last_focus")),
    "rank_workspace_entries": ToolArgumentRule(requires_path_context=True, path_recovery=("workspace_root", "intent_target", "last_focus")),
    "apply_text_patch": ToolArgumentRule(required=("search_text",), requires_path_context=True, path_recovery=("intent_target",)),
    "append_workspace_text": ToolArgumentRule(required=("content",), requires_path_context=True, path_recovery=("intent_target",)),
    "move_workspace_path": ToolArgumentRule(required=("destination_path",), requires_path_context=True, path_recovery=("intent_target",)),
    "delete_workspace_path": ToolArgumentRule(requires_path_context=True, path_recovery=("intent_target",)),
    "create_workspace_path": ToolArgumentRule(required=("path",), path_recovery=("intent_target",)),
    "edit_workspace_text": ToolArgumentRule(required=("path",), path_recovery=("intent_target",)),
    "grep_workspace": ToolArgumentRule(required=("pattern",)),
}


def arguments_have_path_context(arguments: dict[str, Any]) -> bool:
    return bool(str(arguments.get("path") or "").strip() or arguments.get("use_last_focus") or arguments.get("use_default_directory"))


def tool_argument_issue(tool_name: str, arguments: dict[str, Any], *, action_label: bool = False) -> str | None:
    rule = TOOL_ARGUMENT_RULES.get(tool_name)
    if rule is None:
        return None
    issue = tool_argument_issue_key(rule, arguments)
    if issue is None:
        return None
    issue_kind, issue_value = issue
    qualifier = " action" if action_label else ""
    if issue_kind == "path_context":
        return f"{tool_name}{qualifier} is missing path context"
    return f"{tool_name}{qualifier} is missing {issue_value}"


def tool_argument_issue_key(rule: ToolArgumentRule, arguments: dict[str, Any]) -> tuple[str, str] | None:
    if rule.requires_path_context and not arguments_have_path_context(arguments):
        return ("path_context", "")
    for key in rule.required:
        if not str(arguments.get(key) or "").strip():
            return ("required", key)
    return None


def repair_tool_path_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    intent_target: str = "",
    scope_kind: str = "",
    has_last_focus: bool = False,
) -> tuple[dict[str, Any], str | None]:
    repaired = dict(arguments)
    rule = TOOL_ARGUMENT_RULES.get(tool_name)
    if rule is None or not rule.path_recovery:
        return repaired, None
    if str(repaired.get("path") or "").strip():
        return repaired, None
    if rule.requires_path_context and (repaired.get("use_last_focus") or repaired.get("use_default_directory")):
        return repaired, None

    for source in rule.path_recovery:
        if source == "workspace_root" and (intent_target == "." or scope_kind == "workspace_root"):
            repaired["path"] = "."
            repaired["use_default_directory"] = True
            return repaired, source
        if source == "intent_target" and intent_target:
            repaired["path"] = intent_target
            return repaired, source
        if source == "last_focus" and has_last_focus:
            repaired["use_last_focus"] = True
            return repaired, source
    return repaired, None
