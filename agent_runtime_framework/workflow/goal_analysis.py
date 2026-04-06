from __future__ import annotations

import json
import re
from typing import Any

from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.llm_access import get_application_context
from agent_runtime_framework.workflow.models import GoalSpec
from agent_runtime_framework.workflow.planner_prompts import build_goal_analysis_system_prompt
from agent_runtime_framework.workflow.prompting import extract_json_block


README_PATTERN = re.compile(r"readme(?:\.md)?", re.IGNORECASE)
CHANGE_PATTERN = re.compile(r"(修改|编辑|更新|重构|重写|创建|新增|补充|修复)", re.IGNORECASE)
DESTRUCTIVE_PATTERN = re.compile(r"(删除|移除|清空|卸载)", re.IGNORECASE)
VERIFY_PATTERN = re.compile(r"(验证|校验|检查|确认|测试)", re.IGNORECASE)
READ_PATTERN = re.compile(r"(读取|查看|看看|总结|概括|解释|讲解|分析)", re.IGNORECASE)
PATH_FRAGMENT_PATTERN = re.compile(
    r"([A-Za-z0-9_\-./]+\.[A-Za-z0-9_\-]+|[A-Za-z0-9_\-]+/[A-Za-z0-9_\-./]+)"
)


def _extract_target_hint(user_input: str) -> str:
    for match in PATH_FRAGMENT_PATTERN.finditer(user_input):
        candidate = match.group(1).strip('`"，。,. ')
        if not candidate:
            continue
        if candidate.endswith(".") or candidate.startswith("."):
            continue
        return candidate
    return ""


def analyze_goal(user_input: str, context: Any | None = None) -> GoalSpec:
    text = user_input.strip()
    if not text:
        raise RuntimeError("planner model unavailable for goal analysis: empty input")

    llm_goal, error_reason = _analyze_goal_with_model(text, context=context)
    if llm_goal is not None:
        return llm_goal
    raise RuntimeError(f"planner model unavailable for goal analysis: {error_reason or 'unknown error'}")


def _analyze_goal_with_model(user_input: str, *, context: Any | None) -> tuple[GoalSpec | None, str | None]:
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
                        content=build_goal_analysis_system_prompt(),
                    ),
                    ChatMessage(role="user", content=user_input),
                ],
                temperature=0.0,
                max_tokens=300,
            ),
        )
    except Exception as exc:
        return None, str(exc) or "model call failed"

    try:
        parsed = json.loads(extract_json_block(str(response.content or "")))
    except Exception:
        return None, "invalid model response"

    primary_intent = str(parsed.get("primary_intent") or "").strip()
    if not primary_intent:
        return None, "invalid model response"
    target_paths = [str(item).strip() for item in parsed.get("target_paths") or [] if str(item).strip()]
    return GoalSpec(
        original_goal=user_input,
        primary_intent=primary_intent,
        requires_repository_overview=bool(parsed.get("requires_repository_overview")),
        requires_file_read=bool(parsed.get("requires_file_read")),
        requires_final_synthesis=bool(parsed.get("requires_final_synthesis")),
        target_paths=target_paths,
        metadata=dict(parsed.get("metadata") or {}),
    ), None
