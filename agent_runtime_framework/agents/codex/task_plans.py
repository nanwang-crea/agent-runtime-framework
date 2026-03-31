from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction, CodexPlan, CodexPlanTask, CodexTask, TargetSemantics
from agent_runtime_framework.agents.codex.answer_synthesizer import build_synthesized_response_action, synthesize_answer
from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import extract_json_block
from agent_runtime_framework.agents.codex.run_context import available_tool_names
from agent_runtime_framework.agents.codex.semantics import goal_is_raw_read, goal_prefers_summary, infer_task_intent, repository_target_hint
from agent_runtime_framework.agents.codex.workflows import workflow_name_for_task_profile
from agent_runtime_framework.models import resolve_model_runtime


def build_task_plan(task: CodexTask, context: Any) -> CodexPlan | None:
    persona = resolve_runtime_persona(context, task=task)
    tool_names = set(available_tool_names(context, persona=persona))
    workspace_root = str(Path(str(context.application_context.config.get("default_directory") or "")).expanduser().resolve() if context.application_context.config.get("default_directory") else "")
    intent = task.intent
    builder = _fallback_plan_builder(intent)
    if builder is None:
        return None
    return builder(task, tool_names=tool_names, workspace_root=workspace_root, intent=intent)


def _fallback_plan_builder(intent: Any):
    strategy_families = list(getattr(intent, "allowed_strategy_family", []) or [])
    if any(item in {"workspace_overview", "repository_overview"} for item in strategy_families):
        return _build_repository_plan_fallback
    if "file_reader" in strategy_families:
        return _build_file_reader_plan_fallback
    if any(item in {"locate_modify_verify", "clarify_then_modify"} for item in strategy_families):
        return _build_change_plan_fallback
    return {
        "repository_explainer": _build_repository_plan_fallback,
        "file_reader": _build_file_reader_plan_fallback,
        "change_and_verify": _build_change_plan_fallback,
    }.get(str(getattr(intent, "task_kind", "") or ""))


def _build_repository_plan_fallback(task: CodexTask, *, tool_names: set[str], workspace_root: str, intent: Any) -> CodexPlan | None:
    if "list_workspace_directory" not in tool_names:
        return None
    target = intent.target_ref or intent.target_hint or _extract_repository_target(task.goal)
    goal_mode = str(getattr(intent, "goal_mode", "") or "")
    is_workspace_root = target == "."
    tasks: list[CodexPlanTask] = []
    dependency_ids: list[str] = []
    if not is_workspace_root:
        locate_step = CodexPlanTask(
            title="Locate repository target",
            kind="locate_target",
            metadata={"target_hint": target, "query": task.goal, "use_default_directory": not target},
        )
        tasks.append(locate_step)
        dependency_ids = [locate_step.task_id]
    if goal_mode in {"workspace_overview", "project_summary"} and "inspect_workspace_path" in tool_names:
        inspect_step = CodexPlanTask(
            title="Inspect repository target",
            kind="inspect_target",
            depends_on=list(dependency_ids),
            metadata={"path": target or ".", "use_last_focus": not is_workspace_root},
        )
        tasks.append(inspect_step)
        dependency_ids = [inspect_step.task_id]
        if "rank_workspace_entries" in tool_names:
            rank_step = CodexPlanTask(
                title="Rank representative files",
                kind="rank_representative_files",
                depends_on=list(dependency_ids),
                metadata={"path": target or "."},
            )
            tasks.append(rank_step)
            dependency_ids = [rank_step.task_id]
    gather_step = CodexPlanTask(
        title="Gather repository context",
        kind="gather_context",
        depends_on=list(dependency_ids),
        metadata={
            "path": target or ".",
            "tool_name": "list_workspace_directory",
            "use_default_directory": is_workspace_root,
            "use_resolved_target": not is_workspace_root,
        },
    )
    tasks.append(gather_step)
    tasks.append(CodexPlanTask(title="Synthesize repository overview", kind="synthesize_answer", depends_on=[gather_step.task_id], metadata={"path": target or "."}))
    return CodexPlan(tasks=tasks, metadata={"workspace_root": workspace_root, "workflow": workflow_name_for_task_profile(task.task_profile), "task_intent": intent.as_dict(), "plan_source": "deterministic"})


def _build_file_reader_plan_fallback(task: CodexTask, *, tool_names: set[str], workspace_root: str, intent: Any) -> CodexPlan | None:
    if "resolve_workspace_target" not in tool_names:
        return None
    target = intent.target_ref or intent.target_hint or _extract_repository_target(task.goal)
    preferred_read_tool = "summarize_workspace_text" if goal_prefers_summary(task.goal) and "summarize_workspace_text" in tool_names else "read_workspace_text"
    locate_step = CodexPlanTask(title="Locate file target", kind="locate_target", metadata={"target_hint": target, "query": task.goal, "use_default_directory": not target})
    gather_step = CodexPlanTask(title="Gather file contents", kind="gather_context", depends_on=[locate_step.task_id], metadata={"path": target, "use_default_directory": not target, "use_resolved_target": True, "preferred_read_tool": preferred_read_tool})
    tasks = [locate_step, gather_step, CodexPlanTask(title="Synthesize file summary", kind="synthesize_answer", depends_on=[gather_step.task_id], metadata={"path": target})]
    return CodexPlan(tasks=tasks, metadata={"workspace_root": workspace_root, "workflow": workflow_name_for_task_profile(task.task_profile), "task_intent": intent.as_dict(), "plan_source": "deterministic"})


def _build_change_plan_fallback(task: CodexTask, *, tool_names: set[str], workspace_root: str, intent: Any) -> CodexPlan | None:
    locate_step = CodexPlanTask(title="Locate change target", kind="locate_target", metadata={"target_hint": _extract_change_target(task.goal), "query": task.goal})
    edit_step = _build_change_edit_task(task.goal, tool_names)
    verify_step = _build_change_verify_task(task.goal, tool_names)
    if edit_step is None:
        return None
    edit_step.depends_on = [locate_step.task_id]
    edit_step.metadata["use_resolved_target"] = str(edit_step.metadata.get("tool_name") or "") != "create_workspace_path"
    tasks = [locate_step, edit_step]
    if verify_step is not None:
        verify_step.depends_on = [edit_step.task_id]
        tasks.append(verify_step)
    tasks.append(CodexPlanTask(title="Synthesize change summary", kind="synthesize_answer", depends_on=[tasks[-1].task_id]))
    return CodexPlan(tasks=tasks, metadata={"workspace_root": workspace_root, "workflow": workflow_name_for_task_profile(task.task_profile), "task_intent": intent.as_dict(), "plan_source": "deterministic"})



def _build_change_edit_task(goal: str, tool_names: set[str]) -> CodexPlanTask | None:
    replace_match = re.search(r'([A-Za-z0-9_./-]+).*?[“"]([^“”"]+)[”"].*?(?:替换成|替换为|替换|改成|为|replace(?:\s+with)?)\s*[“"]([^“”"]+)[”"]', goal)
    if replace_match and "apply_text_patch" in tool_names:
        return CodexPlanTask(
            title="Patch target text",
            kind="modify_target",
            metadata={
                "tool_name": "apply_text_patch",
                "arguments": {
                    "path": replace_match.group(1),
                    "search_text": replace_match.group(2),
                    "replace_text": replace_match.group(3),
                },
            },
        )
    append_match = re.search(r'([A-Za-z0-9_./-]+).*(?:追加|append).*?[“"]([^“”"]*)[”"]', goal)
    if append_match and "append_workspace_text" in tool_names:
        return CodexPlanTask(
            title="Append workspace text",
            kind="modify_target",
            metadata={
                "tool_name": "append_workspace_text",
                "arguments": {
                    "path": append_match.group(1),
                    "content": append_match.group(2).encode("utf-8").decode("unicode_escape"),
                },
            },
        )
    move_match = re.search(r'把\s*([A-Za-z0-9_./-]+)\s*移动到\s*([A-Za-z0-9_./-]+)', goal)
    if move_match and "move_workspace_path" in tool_names:
        return CodexPlanTask(
            title="Move workspace path",
            kind="modify_target",
            metadata={
                "tool_name": "move_workspace_path",
                "arguments": {"path": move_match.group(1), "destination_path": move_match.group(2)},
            },
        )
    delete_match = re.search(r'(?:删除|delete)\s*([A-Za-z0-9_./-]+)', goal)
    if delete_match and "delete_workspace_path" in tool_names:
        return CodexPlanTask(
            title="Delete workspace path",
            kind="modify_target",
            metadata={"tool_name": "delete_workspace_path", "arguments": {"path": delete_match.group(1)}},
        )
    create_match = re.search(r'(?:创建|新建)\s*([A-Za-z0-9_./-]+)(?:\s*内容\s*(.+))?$', goal)
    if create_match and "create_workspace_path" in tool_names:
        return CodexPlanTask(
            title="Create workspace path",
            kind="modify_target",
            metadata={
                "tool_name": "create_workspace_path",
                "arguments": {
                    "path": create_match.group(1),
                    "kind": "file",
                    "content": str(create_match.group(2) or "").strip(),
                },
            },
        )
    edit_match = re.search(r'(?:编辑|修改)\s*([A-Za-z0-9_./-]+)(?:\s*内容\s*(.+?))?(?:\s*并运行验证\s+(.+))?$', goal)
    if edit_match and "edit_workspace_text" in tool_names:
        return CodexPlanTask(
            title="Edit workspace text",
            kind="modify_target",
            metadata={
                "tool_name": "edit_workspace_text",
                "arguments": {"path": edit_match.group(1), "content": str(edit_match.group(2) or "").strip()},
            },
        )
    return None


def _build_change_verify_task(goal: str, tool_names: set[str]) -> CodexPlanTask | None:
    if "run_shell_command" not in tool_names:
        return None
    match = re.search(r'(?:运行验证|验证|run verification)\s+(.+)$', goal, flags=re.IGNORECASE)
    command = str(match.group(1) if match else "").strip()
    if not command:
        return None
    return CodexPlanTask(
        title="Run verification",
        kind="run_verification",
        metadata={"command": command},
    )


def attach_action_to_plan(task: CodexTask, action: CodexAction, action_index: int) -> None:
    plan_task = _find_plan_task(task.plan, str(action.metadata.get("plan_task_id") or ""))
    if plan_task is None:
        return
    if action_index not in plan_task.action_indexes:
        plan_task.action_indexes.append(action_index)
    if plan_task.status == "pending":
        plan_task.status = "in_progress"
    _sync_plan_status(task)


def sync_task_plan(task: CodexTask) -> None:
    _sync_plan_status(task)
    plan = task.plan
    if plan is None:
        task.state.plan_state = {}
        return
    task.state.plan_state = {
        "status": plan.status,
        "tasks": [f"{item.title}:{item.kind}:{item.status}" for item in plan.tasks],
    }
    task.state.pending_actions = [item.kind for item in plan.tasks if item.status != "completed"]
    if plan.target_semantics is None:
        return
    task.state.resource_semantics = {
        "path": plan.target_semantics.path,
        "resource_kind": plan.target_semantics.resource_kind,
        "is_container": plan.target_semantics.is_container,
        "allowed_actions": list(plan.target_semantics.allowed_actions),
    }
    if plan.target_semantics.path:
        task.state.resolved_target = plan.target_semantics.path


def advance_task_plan(task: CodexTask, action: CodexAction, result: Any, context: Any) -> None:
    plan = task.plan
    if plan is None:
        return
    plan_task = _find_plan_task(plan, str(action.metadata.get("plan_task_id") or ""))
    if plan_task is None:
        return
    if action.kind == "call_tool" and str(action.metadata.get("tool_name") or "") == "resolve_workspace_target":
        tool_output = dict(getattr(result, "metadata", {}).get("tool_output") or {})
        resolution_status = str(tool_output.get("resolution_status") or "resolved").strip() or "resolved"
        if resolution_status in {"ambiguous", "unresolved"}:
            if resolution_status == "unresolved" and _allows_missing_target_creation(plan):
                _sync_plan_status(task)
                return
            clarification = str(tool_output.get("text") or getattr(result, "final_output", "") or "").strip()
            _block_follow_up_plan_tasks(plan)
            if _find_task_by_kind(plan, "clarify_target") is None:
                plan.tasks.append(
                    CodexPlanTask(
                        title="Clarify target",
                        kind="clarify_target",
                        depends_on=[plan_task.task_id],
                        metadata={"message": clarification or "Please provide a more specific goal."},
                    )
                )
            _sync_plan_status(task)
            return
        resolved_path = str(tool_output.get("resolved_path") or tool_output.get("path") or "").strip()
        semantics = _target_semantics_from_tool_output(tool_output)
        if resolved_path:
            plan.metadata["resolved_path"] = resolved_path
            _propagate_resolved_path(plan, resolved_path)
        if semantics is not None:
            plan.target_semantics = semantics
            plan.metadata["resource_kind"] = semantics.resource_kind
            _propagate_target_semantics(plan, semantics)
            if task.task_profile == "repository_explainer" and semantics.resource_kind == "file":
                _rewrite_repository_plan_for_file_target(plan, resolved_path, depends_on=[plan_task.task_id])
        if (
            task.task_profile == "repository_explainer"
            and resolved_path
            and semantics is not None
            and semantics.is_container
            and "inspect_workspace_path" in set(context.application_context.tools.names())
            and _find_task_by_kind(plan, "inspect_target") is None
        ):
            _insert_task_before_kind(
                plan,
                before_kind="synthesize_answer",
                new_task=CodexPlanTask(
                    title="Inspect resolved target",
                    kind="inspect_target",
                    depends_on=[plan_task.task_id],
                    metadata={"path": resolved_path, "use_last_focus": False},
                ),
            )
    if (
        action.kind == "call_tool"
        and str(action.metadata.get("tool_name") or "") == "rank_workspace_entries"
        and task.task_profile == "repository_explainer"
        and str(plan.metadata.get("workflow") or "") == "repository_overview"
        and "extract_workspace_outline" in set(context.application_context.tools.names())
    ):
        ranked_paths = [
            str(item).strip()
            for item in (getattr(result, "metadata", {}).get("tool_output") or {}).get("ranked_paths") or []
            if str(item).strip()
        ]
        rank_task_id = plan_task.task_id if plan_task is not None else ""
        for ranked_path in ranked_paths[:2]:
            if _has_extract_outline_for_path(plan, ranked_path):
                continue
            _insert_task_before_kind(
                plan,
                before_kind="synthesize_answer",
                new_task=CodexPlanTask(
                    title=f"Extract outline for {ranked_path}",
                    kind="extract_outline",
                    depends_on=[rank_task_id] if rank_task_id else [],
                    metadata={"path": ranked_path},
                ),
            )
    if action.kind == "run_verification" and getattr(result, "status", "") == "failed":
        for suggested in _suggest_failed_verification_recovery_with_llm(task, action, result, context):
            if _find_task_by_kind(plan, suggested.kind) is not None:
                continue
            _insert_task_before_kind(plan, before_kind="synthesize_answer", new_task=suggested)
            command = str(action.metadata.get("command") or action.instruction or "").strip()
            if command:
                _insert_task_before_kind(
                    plan,
                    before_kind="synthesize_answer",
                    new_task=CodexPlanTask(
                        title="Re-run verification",
                        kind="run_verification",
                        depends_on=[suggested.task_id],
                        metadata={"command": command},
                    ),
                )
    if getattr(result, "status", "") == "failed" and action.kind != "run_verification":
        for suggested in _suggest_failed_action_recovery_with_llm(task, action, result, context):
            if _find_task_by_kind(plan, suggested.kind) is not None:
                continue
            _insert_task_before_kind(plan, before_kind="synthesize_answer", new_task=suggested)
    llm_tasks = _suggest_follow_up_tasks_with_llm(task, action, result, context)
    for suggested in llm_tasks:
        if _find_task_by_kind(plan, suggested.kind) is not None:
            continue
        _insert_task_before_kind(plan, before_kind="synthesize_answer", new_task=suggested)
    _sync_plan_status(task)


def _sync_plan_status(task: CodexTask) -> None:
    plan = task.plan
    if plan is None:
        return
    for plan_task in plan.tasks:
        if not plan_task.action_indexes:
            if plan_task.status == "in_progress":
                plan_task.status = "pending"
            continue
        statuses = [task.actions[index].status for index in plan_task.action_indexes if index < len(task.actions)]
        if not statuses:
            continue
        if any(status == "completed" for status in statuses) and all(status == "completed" for status in statuses):
            plan_task.status = "completed"
        elif any(status in {"failed", "cancelled"} for status in statuses):
            plan_task.status = "blocked"
        elif any(status == "awaiting_approval" for status in statuses):
            plan_task.status = "in_progress"
        else:
            plan_task.status = "in_progress"
    if plan.tasks and all(item.status == "completed" for item in plan.tasks):
        plan.status = "completed"
    elif any(item.status == "blocked" for item in plan.tasks):
        plan.status = "blocked"
    elif any(item.status == "in_progress" for item in plan.tasks):
        plan.status = "in_progress"
    else:
        plan.status = "pending"


def _next_plan_task(plan: CodexPlan) -> CodexPlanTask | None:
    completed = {task.task_id for task in plan.tasks if task.status == "completed"}
    for plan_task in plan.tasks:
        if plan_task.status in {"completed", "blocked"}:
            continue
        if any(task_id not in completed for task_id in plan_task.depends_on):
            continue
        return plan_task
    return None


def has_pending_plan_task(task: CodexTask) -> bool:
    plan = task.plan
    if plan is None:
        return False
    return _next_plan_task(plan) is not None


def _find_plan_task(plan: CodexPlan | None, task_id: str) -> CodexPlanTask | None:
    if plan is None or not task_id:
        return None
    for plan_task in plan.tasks:
        if plan_task.task_id == task_id:
            return plan_task
    return None


def _find_task_by_kind(plan: CodexPlan, kind: str) -> CodexPlanTask | None:
    for plan_task in plan.tasks:
        if plan_task.kind == kind:
            return plan_task
    return None


def _has_extract_outline_for_path(plan: CodexPlan, path: str) -> bool:
    normalized = str(path).strip()
    for plan_task in plan.tasks:
        if plan_task.kind != "extract_outline":
            continue
        if str(plan_task.metadata.get("path") or "").strip() == normalized:
            return True
    return False


def _insert_task_before_kind(plan: CodexPlan, *, before_kind: str, new_task: CodexPlanTask) -> None:
    for index, plan_task in enumerate(plan.tasks):
        if plan_task.kind != before_kind:
            continue
        existing_depends = list(plan_task.depends_on)
        plan_task.depends_on = [new_task.task_id]
        preserve_empty_depends = bool(new_task.metadata.pop("_preserve_empty_depends", False))
        if preserve_empty_depends:
            new_task.depends_on = list(new_task.depends_on)
        else:
            new_task.depends_on = list(new_task.depends_on or existing_depends)
        plan.tasks.insert(index, new_task)
        return
    plan.tasks.append(new_task)


def _block_follow_up_plan_tasks(plan: CodexPlan) -> None:
    for plan_task in plan.tasks:
        if plan_task.kind == "locate_target":
            continue
        plan_task.status = "blocked"


def _allows_missing_target_creation(plan: CodexPlan) -> bool:
    modify_task = _find_task_by_kind(plan, "modify_target")
    if modify_task is None:
        return False
    return str(modify_task.metadata.get("tool_name") or "") == "create_workspace_path"


def _extract_repository_target(goal: str) -> str:
    return repository_target_hint(goal)


def _is_list_only_request(goal: str) -> bool:
    return False


def _extract_change_target(goal: str) -> str:
    return ""
    return ""
def _resolved_plan_path(plan_task: CodexPlanTask) -> str:
    return str(plan_task.metadata.get("resolved_path") or plan_task.metadata.get("path") or "")


def _resolved_modify_arguments(plan_task: CodexPlanTask) -> dict[str, Any]:
    arguments = dict(plan_task.metadata.get("arguments") or {})
    if plan_task.metadata.get("use_resolved_target") and plan_task.metadata.get("resolved_path"):
        arguments["path"] = str(plan_task.metadata.get("resolved_path"))
    return arguments


def _propagate_resolved_path(plan: CodexPlan, resolved_path: str) -> None:
    for plan_task in plan.tasks:
        if plan_task.kind == "locate_target":
            continue
        if plan_task.metadata.get("use_resolved_target"):
            plan_task.metadata["resolved_path"] = resolved_path


def _propagate_target_semantics(plan: CodexPlan, semantics: TargetSemantics) -> None:
    for plan_task in plan.tasks:
        if plan_task.kind == "locate_target":
            continue
        plan_task.metadata["resource_kind"] = semantics.resource_kind
        plan_task.metadata["allowed_actions"] = list(semantics.allowed_actions)
        plan_task.metadata["is_container"] = semantics.is_container
        if plan_task.metadata.get("use_resolved_target") and semantics.path:
            plan_task.metadata["resolved_path"] = semantics.path


def _rewrite_repository_plan_for_file_target(plan: CodexPlan, resolved_path: str, *, depends_on: list[str]) -> None:
    rewritten: list[CodexPlanTask] = []
    synth_task = _find_task_by_kind(plan, "synthesize_answer")
    for plan_task in plan.tasks:
        if plan_task.kind == "locate_target":
            rewritten.append(plan_task)
            continue
        if plan_task.kind == "gather_context":
            plan_task.depends_on = list(depends_on)
            plan_task.metadata["resource_kind"] = "file"
            plan_task.metadata["path"] = resolved_path
            plan_task.metadata["resolved_path"] = resolved_path
            plan_task.metadata["tool_name"] = "read_workspace_text"
            rewritten.append(plan_task)
    if synth_task is not None and rewritten:
        synth_task.depends_on = [rewritten[-1].task_id]
        rewritten.append(synth_task)
    plan.tasks = rewritten


def _target_semantics_from_tool_output(tool_output: dict[str, Any]) -> TargetSemantics | None:
    resolved_path = str(tool_output.get("resolved_path") or tool_output.get("path") or "").strip()
    resource_kind = str(tool_output.get("resource_kind") or "").strip()
    if not resolved_path or not resource_kind:
        return None
    allowed_actions = [str(action).strip() for action in tool_output.get("allowed_actions") or [] if str(action).strip()]
    return TargetSemantics(
        path=resolved_path,
        resource_kind=resource_kind,
        is_container=bool(tool_output.get("is_container") or resource_kind == "directory"),
        allowed_actions=allowed_actions,
    )


def _build_synthesized_answer(task: CodexTask) -> str:
    return synthesize_answer(task)


def _build_repository_overview(task: CodexTask) -> str:
    structure_claims = [claim for claim in task.state.typed_claims if claim.get("kind") == "structure"]
    role_claims = [claim for claim in task.state.typed_claims if claim.get("kind") == "role"]
    lines: list[str] = []
    if structure_claims:
        lines.append(f"目录结构：{structure_claims[0].get('detail', '')}")
    if role_claims:
        for claim in role_claims[:5]:
            subject = claim.get("subject", "").strip()
            detail = claim.get("detail", "").strip()
            if subject and detail:
                lines.append(f"{subject} 的作用：{detail}")
    if not lines:
        fallback = next(
            (action.observation.strip() for action in reversed(task.actions) if (action.observation or "").strip()),
            "",
        )
        if fallback:
            body = f"Here is what I found about `{_extract_repository_target(task.goal) or 'the target directory'}`:\n{fallback}"
            return _append_references(body, task)
        return _append_references(f"Not enough information to explain `{_extract_repository_target(task.goal) or 'the target directory'}` yet.", task)
    body = "根据当前收集到的证据：\n" + "\n".join(f"- {line}" for line in lines)
    return _append_references(body, task)


def _build_change_summary(task: CodexTask) -> str:
    modified = task.state.modified_paths[:3]
    verification = task.verification
    lines: list[str] = []
    latest_modify_output = next(
        (
            action.observation.strip()
            for action in reversed(task.actions)
            if action.kind in {"edit_text", "apply_patch", "create_path"} and (action.observation or "").strip()
        ),
        "",
    )
    if modified:
        lines.append(f"Completed the requested update. Files changed: {', '.join(modified)}")
    if latest_modify_output:
        lines.append(f"Latest content: {latest_modify_output}")
    if verification is not None:
        status = "passed" if verification.success else "failed"
        lines.append(f"Verification: {status} — {verification.summary}")
    else:
        last_verification = next(
            (
                action.observation.strip()
                for action in reversed(task.actions)
                if action.kind == "run_verification" and (action.observation or "").strip()
            ),
            "",
        )
        if last_verification:
            lines.append(f"Verification: {last_verification}")
        else:
            lines.append("Verification: not run.")
    if not lines:
        lines.append("Completed the requested update.")
    body = "Result:\n" + "\n".join(f"- {line}" for line in lines)
    return _append_references(body, task)


def _build_file_reader_summary(task: CodexTask) -> str:
    latest = next(
        (
            action.observation.strip()
            for action in reversed(task.actions)
            if action.kind == "call_tool"
            and str(action.metadata.get("tool_name") or "") in {"read_workspace_text", "summarize_workspace_text", "inspect_workspace_path"}
            and (action.observation or "").strip()
        ),
        "",
    )
    target = _extract_repository_target(task.goal) or "the target file"
    if latest:
        if _goal_is_raw_read(task.goal):
            return latest
        if _goal_prefers_summary(task.goal):
            return _append_references(f"我先基于已读取内容做一个简要说明：\n{latest}", task)
        return _append_references(f"Here is a summary of `{target}`:\n{latest}", task)
    return _append_references(f"Not enough content to summarize `{target}` yet.", task)


def _action_kind_for_tool(tool_name: str) -> str:
    return {
        "edit_workspace_text": "edit_text",
        "append_workspace_text": "edit_text",
        "apply_text_patch": "apply_patch",
        "create_workspace_path": "create_path",
        "move_workspace_path": "move_path",
        "delete_workspace_path": "delete_path",
    }.get(tool_name, "call_tool")


def _suggest_follow_up_tasks_with_llm(task: CodexTask, action: CodexAction, result: Any, context: Any) -> list[CodexPlanTask]:
    if task.task_profile != "repository_explainer":
        return []
    plan = task.plan
    if plan is None:
        return []
    plan_task = _find_plan_task(plan, str(action.metadata.get("plan_task_id") or ""))
    if plan_task is None or plan_task.kind != "inspect_target":
        return []
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        return []
    observation = str(getattr(result, "final_output", "") or "").strip()
    if not observation:
        return []
    plan_kinds = [item.kind for item in plan.tasks]
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=build_codex_system_prompt(
                            render_codex_prompt_doc("task_plan_expander_system")
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=render_codex_prompt_doc(
                            "task_plan_expander_user",
                            goal=task.goal,
                            current_plan=", ".join(plan_kinds),
                            inspect_result=observation,
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=180,
            ),
        )
    except Exception:
        return []
    parsed = _parse_json_payload(str(response.content or ""))
    tasks: list[CodexPlanTask] = []
    for item in parsed.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") != "read_entrypoint":
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        inspect_task = _find_task_by_kind(plan, "inspect_target")
        depends_on = [inspect_task.task_id] if inspect_task is not None else [plan_task.task_id]
        tasks.append(
            CodexPlanTask(
                title=str(item.get("title") or "Read entrypoint"),
                kind="read_entrypoint",
                depends_on=depends_on,
                metadata={"path": path},
            )
        )
    return tasks


def _suggest_failed_verification_recovery_with_llm(task: CodexTask, action: CodexAction, result: Any, context: Any) -> list[CodexPlanTask]:
    plan = task.plan
    if task.task_profile != "change_and_verify" or plan is None:
        return []
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        return []
    verification_output = str(getattr(result, "final_output", "") or "").strip()
    if not verification_output:
        return []
    modified_target = next((path for path in reversed(task.state.modified_paths) if path.strip()), "")
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=build_codex_system_prompt(
                            render_codex_prompt_doc("repair_after_verification_system")
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=render_codex_prompt_doc(
                            "repair_after_verification_user",
                            goal=task.goal,
                            modified_target=modified_target,
                            verification_output=verification_output,
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=220,
            ),
        )
    except Exception:
        return []
    parsed = _parse_json_payload(str(response.content or ""))
    repair_tasks: list[CodexPlanTask] = []
    modify_task = _find_task_by_kind(plan, "modify_target")
    depends_on = [modify_task.task_id] if modify_task is not None else []
    for item in parsed.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") != "repair_after_failed_verification":
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        path = str(item.get("path") or "").strip()
        if tool_name not in {"edit_workspace_text", "apply_text_patch"} or not path:
            continue
        metadata: dict[str, Any] = {
            "tool_name": tool_name,
            "risk_class": "high",
            "use_resolved_target": False,
        }
        if tool_name == "edit_workspace_text":
            metadata["arguments"] = {"path": path, "content": str(item.get("content") or "")}
        else:
            metadata["arguments"] = {
                "path": path,
                "search_text": str(item.get("search_text") or ""),
                "replace_text": str(item.get("replace_text") or ""),
            }
        repair_tasks.append(
            CodexPlanTask(
                title=str(item.get("title") or "Repair after failed verification"),
                kind="repair_after_failed_verification",
                depends_on=depends_on,
                metadata=metadata,
            )
        )
    return repair_tasks


def _suggest_failed_action_recovery_with_llm(task: CodexTask, action: CodexAction, result: Any, context: Any) -> list[CodexPlanTask]:
    plan = task.plan
    if plan is None:
        return []
    plan_task = _find_plan_task(plan, str(action.metadata.get("plan_task_id") or ""))
    if plan_task is None:
        return []
    error_payload = dict(getattr(result, "metadata", {}).get("error") or {})
    failure_summary = str(getattr(result, "final_output", "") or error_payload.get("message") or "").strip()
    if not failure_summary:
        return []
    if not bool(error_payload.get("retriable") or error_payload.get("suggestion")):
        return []
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        return []
    plan_kinds = [item.kind for item in plan.tasks]
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=build_codex_system_prompt(
                            render_codex_prompt_doc("failed_action_recovery_system")
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=render_codex_prompt_doc(
                            "failed_action_recovery_user",
                            goal=task.goal,
                            task_profile=task.task_profile,
                            current_plan=", ".join(plan_kinds),
                            failed_plan_task=plan_task.kind,
                            failed_action_kind=action.kind,
                            failed_tool=action.metadata.get("tool_name") or "",
                            failure_summary=failure_summary,
                            error_details=json.dumps(error_payload),
                            available_tools=", ".join(context.application_context.tools.names()),
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=240,
            ),
        )
    except Exception:
        return []
    parsed = _parse_json_payload(str(response.content or ""))
    recovery_tasks: list[CodexPlanTask] = []
    for item in parsed.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") != "recover_failed_action":
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        arguments = item.get("arguments")
        if not tool_name or not isinstance(arguments, dict):
            continue
        recovery_tasks.append(
            CodexPlanTask(
                title=str(item.get("title") or "Recover failed action"),
                kind="recover_failed_action",
                depends_on=list(plan_task.depends_on),
                metadata={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "risk_class": str(item.get("risk_class") or "low"),
                    "subgoal": str(item.get("subgoal") or "gather_evidence"),
                    "_preserve_empty_depends": True,
                },
            )
        )
    return recovery_tasks


def _append_references(body: str, task: CodexTask) -> str:
    references = _collect_reference_labels(task)
    if not references:
        return body
    return body + "\n引用：\n" + "\n".join(f"- {item}" for item in references)


def _collect_reference_labels(task: CodexTask) -> list[str]:
    labels: list[str] = []
    root = ""
    plan = task.plan
    if plan is not None:
        root = str(plan.metadata.get("workspace_root") or "")
    if not root:
        root = ""
    for path in [*task.state.read_paths, *task.state.modified_paths]:
        normalized = _normalize_reference_path(path, root)
        if normalized and normalized not in labels:
            labels.append(normalized)
    for action in task.actions:
        if action.kind == "run_verification":
            command = str(action.metadata.get("command") or action.instruction or "").strip()
            if command and f"command:{command}" not in labels:
                labels.append(f"command:{command}")
    return labels


def _normalize_reference_path(path: str, root: str) -> str:
    normalized = path.strip()
    if not normalized:
        return ""
    if root and normalized.startswith(root.rstrip("/") + "/"):
        return normalized[len(root.rstrip("/") + "/") :]
    return normalized


def _parse_json_payload(content: str) -> dict[str, Any]:
    stripped = extract_json_block(content)
    try:
        parsed = json.loads(stripped.strip())
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
