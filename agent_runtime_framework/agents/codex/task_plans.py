from __future__ import annotations

import json
import re
from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction, CodexPlan, CodexPlanTask, CodexTask, TargetSemantics
from agent_runtime_framework.agents.codex.profiles import extract_workspace_target_hint
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime


def build_task_plan(task: CodexTask, context: Any) -> CodexPlan | None:
    tool_names = set(context.application_context.tools.names())
    workspace_root = str(context.application_context.config.get("default_directory") or "")
    if task.task_profile == "repository_explainer":
        if "inspect_workspace_path" not in tool_names:
            return None
        target = _extract_repository_target(task.goal)
        locate_step = CodexPlanTask(
            title="Locate repository target",
            kind="locate_target",
            metadata={"target_hint": target, "query": task.goal, "use_default_directory": not target},
        )
        gather_step = CodexPlanTask(
            title="Gather repository context",
            kind="gather_context",
            depends_on=[locate_step.task_id],
            metadata={"path": target, "use_default_directory": not target, "use_resolved_target": True},
        )
        tasks = [locate_step, gather_step]
        tasks.append(
            CodexPlanTask(
                title="Synthesize repository overview",
                kind="synthesize_answer",
                depends_on=[tasks[-1].task_id],
                metadata={"path": target},
            )
        )
        return CodexPlan(tasks=tasks, metadata={"workspace_root": workspace_root})
    if task.task_profile == "change_and_verify":
        locate_step = CodexPlanTask(
            title="Locate change target",
            kind="locate_target",
            metadata={"target_hint": _extract_change_target(task.goal), "query": task.goal},
        )
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
        tasks.append(
            CodexPlanTask(
                title="Synthesize change summary",
                kind="synthesize_answer",
                depends_on=[tasks[-1].task_id],
            )
        )
        return CodexPlan(tasks=tasks, metadata={"workspace_root": workspace_root})
    return None


def _build_change_edit_task(goal: str, tool_names: set[str]) -> CodexPlanTask | None:
    patch_match = re.search(
        r'把\s+([^\s]+)\s+里(?:的)?\s+"([^"]+)"\s+替换成\s+"([^"]+)"',
        goal,
    )
    if patch_match and "apply_text_patch" in tool_names:
        path, search_text, replace_text = patch_match.groups()
        return CodexPlanTask(
            title="Modify target with patch",
            kind="modify_target",
            metadata={
                "tool_name": "apply_text_patch",
                "arguments": {
                    "path": path,
                    "search_text": search_text,
                    "replace_text": replace_text,
                },
                "risk_class": "high",
            },
        )
    edit_match = re.search(r"(?:编辑|修改)\s+([^\s]+)\s+内容\s+(.+?)(?:\s+并运行验证\s+.+)?$", goal)
    if edit_match and "edit_workspace_text" in tool_names:
        path, content = edit_match.groups()
        return CodexPlanTask(
            title="Modify target contents",
            kind="modify_target",
            metadata={
                "tool_name": "edit_workspace_text",
                "arguments": {"path": path, "content": content.strip()},
                "risk_class": "high",
            },
        )
    create_match = re.search(r"(?:创建|新建)\s+([^\s]+)(?:\s+内容\s+(.+))?", goal)
    if create_match and "create_workspace_path" in tool_names:
        path, content = create_match.groups()
        return CodexPlanTask(
            title="Create target path",
            kind="modify_target",
            metadata={
                "tool_name": "create_workspace_path",
                "arguments": {"path": path, "content": (content or "").strip(), "kind": "file"},
                "risk_class": "high",
            },
        )
    return None


def _build_change_verify_task(goal: str, tool_names: set[str]) -> CodexPlanTask | None:
    if "run_shell_command" not in tool_names:
        return None
    verification_match = re.search(r"(?:并)?运行(?:验证|测试)\s+(.+)$", goal)
    if not verification_match:
        return None
    command = verification_match.group(1).strip()
    if not command:
        return None
    return CodexPlanTask(
        title="Run verification",
        kind="run_verification",
        metadata={"command": command},
    )


def plan_next_task_action(task: CodexTask) -> CodexAction | None:
    plan = task.plan
    if plan is None:
        return None
    _sync_plan_status(task)
    next_task = _next_plan_task(plan)
    if next_task is None:
        return None
    if next_task.kind == "locate_target":
        return CodexAction(
            kind="call_tool",
            instruction=task.goal,
            subgoal="gather_evidence",
            metadata={
                "tool_name": "resolve_workspace_target",
                "arguments": {
                    "query": str(next_task.metadata.get("query") or task.goal),
                    "target_hint": str(next_task.metadata.get("target_hint") or ""),
                },
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "gather_context":
        resource_kind = str(next_task.metadata.get("resource_kind") or "")
        tool_name = "read_workspace_text" if resource_kind == "file" else "list_workspace_directory"
        arguments = {"path": _resolved_plan_path(next_task)}
        if tool_name == "list_workspace_directory":
            arguments["use_default_directory"] = bool(next_task.metadata.get("use_default_directory"))
        return CodexAction(
            kind="call_tool",
            instruction=task.goal,
            subgoal="gather_evidence",
            metadata={
                "tool_name": tool_name,
                "arguments": arguments,
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "inspect_target":
        return CodexAction(
            kind="call_tool",
            instruction=task.goal,
            subgoal="gather_evidence",
            metadata={
                "tool_name": "inspect_workspace_path",
                "arguments": {
                    "path": _resolved_plan_path(next_task),
                    "use_last_focus": bool(next_task.metadata.get("use_last_focus", True)),
                },
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "read_entrypoint":
        return CodexAction(
            kind="call_tool",
            instruction=task.goal,
            subgoal="gather_evidence",
            metadata={
                "tool_name": "read_workspace_text",
                "arguments": {"path": _resolved_plan_path(next_task)},
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "modify_target":
        return CodexAction(
            kind=_action_kind_for_tool(str(next_task.metadata.get("tool_name") or "")),
            instruction=task.goal,
            subgoal="modify_workspace",
            risk_class=str(next_task.metadata.get("risk_class") or "high"),
            metadata={
                "tool_name": str(next_task.metadata.get("tool_name") or ""),
                "arguments": _resolved_modify_arguments(next_task),
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "repair_after_failed_verification":
        return CodexAction(
            kind=_action_kind_for_tool(str(next_task.metadata.get("tool_name") or "")),
            instruction=task.goal,
            subgoal="modify_workspace",
            risk_class=str(next_task.metadata.get("risk_class") or "high"),
            metadata={
                "tool_name": str(next_task.metadata.get("tool_name") or ""),
                "arguments": dict(next_task.metadata.get("arguments") or {}),
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "recover_failed_action":
        return CodexAction(
            kind=_action_kind_for_tool(str(next_task.metadata.get("tool_name") or "")),
            instruction=task.goal,
            subgoal=str(next_task.metadata.get("subgoal") or "gather_evidence"),
            risk_class=str(next_task.metadata.get("risk_class") or "low"),
            metadata={
                "tool_name": str(next_task.metadata.get("tool_name") or ""),
                "arguments": dict(next_task.metadata.get("arguments") or {}),
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "clarify_target":
        return CodexAction(
            kind="respond",
            instruction=str(next_task.metadata.get("message") or "请补充更明确的目标。"),
            subgoal="synthesize_answer",
            metadata={
                "direct_output": True,
                "clarification_required": True,
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "run_verification":
        return CodexAction(
            kind="run_verification",
            instruction=str(next_task.metadata.get("command") or ""),
            subgoal="verify_changes",
            metadata={
                "command": str(next_task.metadata.get("command") or ""),
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    if next_task.kind == "synthesize_answer":
        return CodexAction(
            kind="respond",
            instruction=_build_synthesized_answer(task),
            subgoal="synthesize_answer",
            metadata={
                "direct_output": True,
                "plan_task_id": next_task.task_id,
                "plan_source": "task_plan",
            },
        )
    return None


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
                        metadata={"message": clarification or "请补充更明确的目标。"},
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
    if action.kind == "run_verification" and getattr(result, "status", "") == "failed":
        for suggested in _suggest_failed_verification_recovery_with_llm(task, action, result, context):
            if _find_task_by_kind(plan, suggested.kind) is not None:
                continue
            _insert_task_before_kind(plan, before_kind="synthesize_answer", new_task=suggested)
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
    return extract_workspace_target_hint(goal)


def _extract_change_target(goal: str) -> str:
    patch_match = re.search(r'把\s+([^\s]+)\s+里(?:的)?\s+"([^"]+)"\s+替换成\s+"([^"]+)"', goal)
    if patch_match:
        return patch_match.group(1).strip()
    edit_match = re.search(r"(?:编辑|修改)\s+([^\s]+)\s+内容\s+(.+?)(?:\s+并运行验证\s+.+)?$", goal)
    if edit_match:
        return edit_match.group(1).strip()
    create_match = re.search(r"(?:创建|新建)\s+([^\s]+)(?:\s+内容\s+(.+))?", goal)
    if create_match:
        return create_match.group(1).strip()
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
    if task.task_profile == "repository_explainer":
        return _build_repository_overview(task)
    if task.task_profile == "change_and_verify":
        return _build_change_summary(task)
    return next((action.observation or "" for action in reversed(task.actions) if action.observation), task.goal)


def _build_repository_overview(task: CodexTask) -> str:
    structure_claims = [claim for claim in task.memory.typed_claims if claim.get("kind") == "structure"]
    role_claims = [claim for claim in task.memory.typed_claims if claim.get("kind") == "role"]
    lines: list[str] = []
    if structure_claims:
        lines.append(f"目录结构：{structure_claims[0].get('detail', '')}")
    if role_claims:
        for claim in role_claims[:5]:
            subject = claim.get("subject", "").strip()
            detail = claim.get("detail", "").strip()
            if subject and detail:
                lines.append(f"{subject} 的作用是{detail}")
    if not lines:
        fallback = next(
            (action.observation.strip() for action in reversed(task.actions) if (action.observation or "").strip()),
            "",
        )
        if fallback:
            body = f"关于 `{_extract_repository_target(task.goal) or '目标目录'}`，我先整理了这些信息：\n{fallback}"
            return _append_references(body, task)
        return _append_references(f"关于 `{_extract_repository_target(task.goal) or '目标目录'}`，暂时还没有足够信息形成结构化讲解。", task)
    body = "基于已收集的信息，我的总结是：\n" + "\n".join(f"- {line}" for line in lines)
    return _append_references(body, task)


def _build_change_summary(task: CodexTask) -> str:
    modified = task.memory.modified_paths[:3]
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
        lines.append(f"已修改：{', '.join(modified)}")
    if latest_modify_output:
        lines.append(f"最新内容：{latest_modify_output}")
    if verification is not None:
        status = "通过" if verification.success else "失败"
        lines.append(f"验证结果：{status}，{verification.summary}")
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
            lines.append(f"验证结果：{last_verification}")
    if not lines:
        lines.append("修改任务已执行完成。")
    body = "处理结果：\n" + "\n".join(f"- {line}" for line in lines)
    return _append_references(body, task)


def _action_kind_for_tool(tool_name: str) -> str:
    return {
        "edit_workspace_text": "edit_text",
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
                        content=(
                            "你是 Codex task-plan expander。"
                            "请判断 repository_explainer 是否需要在 synthesize_answer 前插入额外任务。"
                            "只输出 JSON，格式为 {\"tasks\":[{\"kind\":\"read_entrypoint\",\"path\":\"...\",\"title\":\"...\"}]} 或 {\"tasks\":[]}。"
                            "只有在当前 inspect 结果显示存在关键入口文件且读取它能明显提升最终讲解时，才返回 read_entrypoint。"
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"任务目标：{task.goal}\n"
                            f"当前 plan：{', '.join(plan_kinds)}\n"
                            f"刚完成的 inspect 结果：\n{observation}\n"
                            "如果建议 read_entrypoint，path 必须是工作区内的具体文件路径。"
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
    modified_target = next((path for path in reversed(task.memory.modified_paths) if path.strip()), "")
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "你是 Codex change-recovery planner。"
                            "当验证失败时，判断是否需要在 synthesize_answer 前插入修复任务。"
                            "只输出 JSON，格式为 {\"tasks\":[{\"kind\":\"repair_after_failed_verification\",\"title\":\"...\",\"tool_name\":\"edit_workspace_text|apply_text_patch\",\"path\":\"...\",\"content\":\"...\",\"search_text\":\"...\",\"replace_text\":\"...\"}]} 或 {\"tasks\":[]}。"
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"任务目标：{task.goal}\n"
                            f"最近修改路径：{modified_target}\n"
                            f"验证失败输出：{verification_output}\n"
                            "如果建议修复任务，必须给出具体 path 和所需参数。"
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
                        content=(
                            "你是 Codex failure-recovery planner。"
                            "当某个 plan 动作失败后，判断是否需要在 synthesize_answer 前插入一个恢复动作。"
                            "只输出 JSON，格式为 "
                            "{\"tasks\":[{\"kind\":\"recover_failed_action\",\"title\":\"...\",\"tool_name\":\"...\","
                            "\"arguments\":{},\"risk_class\":\"low|high\",\"subgoal\":\"gather_evidence|modify_workspace|verify_changes\"}]} "
                            "或 {\"tasks\":[]}。"
                            "只有在插入一个明确动作就能继续推进时才返回任务。"
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"任务目标：{task.goal}\n"
                            f"任务类型：{task.task_profile}\n"
                            f"当前 plan：{', '.join(plan_kinds)}\n"
                            f"失败的 plan task：{plan_task.kind}\n"
                            f"失败动作 kind：{action.kind}\n"
                            f"失败工具：{action.metadata.get('tool_name') or ''}\n"
                            f"失败摘要：{failure_summary}\n"
                            f"错误信息：{json.dumps(error_payload, ensure_ascii=False)}\n"
                            f"可用工具：{', '.join(context.application_context.tools.names())}\n"
                            "如果建议恢复动作，arguments 必须是完整 JSON 对象。"
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
    for path in [*task.memory.read_paths, *task.memory.modified_paths]:
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
    stripped = content.strip()
    if "```" in stripped:
        stripped = stripped.split("```", 1)[-1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rsplit("```", 1)[0]
    try:
        parsed = json.loads(stripped.strip())
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
