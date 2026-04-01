from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.agents.workspace_backend.prompting import extract_json_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.models import GoalSpec, SubTaskSpec


def decompose_goal(goal: GoalSpec, context: Any | None = None) -> list[SubTaskSpec]:
    llm_subtasks = _decompose_goal_with_model(goal, context=context)
    if llm_subtasks is not None:
        return llm_subtasks
    return _decompose_goal_deterministically(goal)


def _decompose_goal_with_model(goal: GoalSpec, *, context: Any | None) -> list[SubTaskSpec] | None:
    if context is None:
        return None
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        return None

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
    except Exception:
        return None

    try:
        parsed = json.loads(extract_json_block(str(response.content or "")))
    except Exception:
        return None

    subtasks_payload = parsed.get("subtasks") or []
    subtasks: list[SubTaskSpec] = []
    for item in subtasks_payload:
        if not isinstance(item, dict):
            return None
        task_id = str(item.get("task_id") or "").strip()
        task_profile = str(item.get("task_profile") or "").strip()
        if not task_id or not task_profile:
            return None
        subtasks.append(
            SubTaskSpec(
                task_id=task_id,
                task_profile=task_profile,
                target=(str(item.get("target")).strip() if item.get("target") is not None else None),
                depends_on=[str(dep).strip() for dep in item.get("depends_on") or [] if str(dep).strip()],
                metadata=dict(item.get("metadata") or {}),
            )
        )
    return subtasks or None


def _decompose_goal_deterministically(goal: GoalSpec) -> list[SubTaskSpec]:
    subtasks: list[SubTaskSpec] = []

    if goal.requires_repository_overview:
        subtasks.append(
            SubTaskSpec(
                task_id="repository_overview",
                task_profile="repository_explainer",
                target=".",
            )
        )

    if goal.requires_file_read:
        subtasks.append(
            SubTaskSpec(
                task_id="file_read",
                task_profile="file_reader",
                target=(goal.target_paths[0] if goal.target_paths else None),
            )
        )

    if goal.requires_final_synthesis:
        subtasks.append(
            SubTaskSpec(
                task_id="final_synthesis",
                task_profile="final_synthesis",
                depends_on=[task.task_id for task in subtasks],
            )
        )

    return subtasks
