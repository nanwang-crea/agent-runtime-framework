from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.llm_access import get_application_context
from agent_runtime_framework.workflow.models import GoalSpec, SubTaskSpec
from agent_runtime_framework.workflow.prompting import extract_json_block


def decompose_goal(goal: GoalSpec, context: Any | None = None) -> list[SubTaskSpec]:
    llm_subtasks, fallback_reason = _decompose_goal_with_model(goal, context=context)
    if llm_subtasks is not None:
        for subtask in llm_subtasks:
            subtask.metadata = {
                **dict(subtask.metadata or {}),
                "strategy": "model",
                "model_role": "planner",
            }
        return llm_subtasks
    deterministic = _decompose_goal_deterministically(goal)
    strategy = "fallback" if fallback_reason else "deterministic"
    for subtask in deterministic:
        subtask.metadata = {
            **dict(subtask.metadata or {}),
            "strategy": strategy,
            "model_role": "planner",
            **({"fallback_reason": fallback_reason} if fallback_reason else {}),
        }
    return deterministic


def _decompose_goal_with_model(goal: GoalSpec, *, context: Any | None) -> tuple[list[SubTaskSpec] | None, str | None]:
    application_context = get_application_context(context)
    if application_context is None:
        return None, None
    runtime = resolve_model_runtime(application_context, "planner")
    llm_client = runtime.client if runtime is not None else application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else application_context.llm_model
    if llm_client is None or not model_name:
        return None, "model unavailable"

    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You decompose a workflow goal into ordered subtasks. "
                            "Return JSON only with key subtasks. Each subtask needs: task_id, task_profile, target, depends_on, metadata."
                        ),
                    ),
                    ChatMessage(role="user", content=json.dumps({
                        "original_goal": goal.original_goal,
                        "primary_intent": goal.primary_intent,
                        "requires_repository_overview": goal.requires_repository_overview,
                        "requires_file_read": goal.requires_file_read,
                        "requires_final_synthesis": goal.requires_final_synthesis,
                        "target_paths": goal.target_paths,
                        "metadata": goal.metadata,
                    }, ensure_ascii=False)),
                ],
                temperature=0.0,
                max_tokens=400,
            ),
        )
    except Exception as exc:
        return None, str(exc) or "model call failed"

    try:
        parsed = json.loads(extract_json_block(str(response.content or "")))
    except Exception:
        return None, "invalid model response"

    subtasks_payload = parsed.get("subtasks") or []
    subtasks: list[SubTaskSpec] = []
    for item in subtasks_payload:
        if not isinstance(item, dict):
            return None, "invalid model response"
        task_id = str(item.get("task_id") or "").strip()
        task_profile = str(item.get("task_profile") or "").strip()
        if not task_id or not task_profile:
            return None, "invalid model response"
        subtasks.append(
            SubTaskSpec(
                task_id=task_id,
                task_profile=task_profile,
                target=(str(item.get("target")).strip() if item.get("target") is not None else None),
                depends_on=[str(dep).strip() for dep in item.get("depends_on") or [] if str(dep).strip()],
                metadata=dict(item.get("metadata") or {}),
            )
        )
    if not subtasks:
        return None, "invalid model response"
    return subtasks, None


def _decompose_goal_deterministically(goal: GoalSpec) -> list[SubTaskSpec]:
    subtasks: list[SubTaskSpec] = []

    if goal.requires_repository_overview:
        subtasks.append(
            SubTaskSpec(
                task_id="workspace_discovery",
                task_profile="workspace_discovery",
                target=".",
            )
        )

    if goal.requires_file_read:
        subtasks.append(
            SubTaskSpec(
                task_id="content_search",
                task_profile="content_search",
                target=(goal.target_paths[0] if goal.target_paths else None),
            )
        )
        subtasks.append(
            SubTaskSpec(
                task_id="chunked_file_read",
                task_profile="chunked_file_read",
                target=(goal.target_paths[0] if goal.target_paths else None),
                depends_on=["content_search"],
            )
        )

    if goal.requires_final_synthesis:
        subtasks.append(
            SubTaskSpec(
                task_id="evidence_synthesis",
                task_profile="evidence_synthesis",
                depends_on=[task.task_id for task in subtasks],
            )
        )

    return subtasks
