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


def _extract_target_hint(user_input: str) -> str:
    for part in user_input.replace("\n", " ").split():
        candidate = part.strip('`"，。,. ')
        if not candidate:
            continue
        if "/" in candidate or "." in candidate:
            return candidate
    return ""


def analyze_goal(user_input: str, context: Any | None = None) -> GoalSpec:
    text = user_input.strip()
    if not text:
        return GoalSpec(original_goal=text, primary_intent="generic", metadata={"strategy": "deterministic"})

    llm_goal, fallback_reason = _analyze_goal_with_model(text, context=context)
    if llm_goal is not None:
        llm_goal.metadata = {
            **dict(llm_goal.metadata or {}),
            "strategy": "model",
            "model_role": "planner",
        }
        return llm_goal
    keyword_goal = _analyze_goal_with_keywords(text)
    keyword_goal.metadata = {
        **dict(keyword_goal.metadata or {}),
        "strategy": ("fallback" if fallback_reason else "deterministic"),
        "model_role": "planner",
        **({"fallback_reason": fallback_reason} if fallback_reason else {}),
    }
    return keyword_goal


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


def _analyze_goal_with_keywords(user_input: str) -> GoalSpec:
    wants_readme = bool(README_PATTERN.search(user_input))
    wants_overview = any(token in user_input for token in ["列一下", "文件夹", "目录", "仓库结构", "当前文件夹", "仓库"])
    wants_summary = any(token in user_input for token in ["总结", "概括", "讲什么", "介绍"])
    target_hint = _extract_target_hint(user_input)
    wants_change = bool(CHANGE_PATTERN.search(user_input))
    wants_destructive_change = bool(DESTRUCTIVE_PATTERN.search(user_input))
    wants_verification = bool(VERIFY_PATTERN.search(user_input))
    wants_read = bool(READ_PATTERN.search(user_input))

    if wants_destructive_change:
        metadata = {"requires_approval": True}
        if wants_verification:
            metadata["requires_verification"] = True
        return GoalSpec(
            original_goal=user_input,
            primary_intent="dangerous_change",
            requires_file_read=bool(target_hint),
            requires_final_synthesis=True,
            target_paths=([target_hint] if target_hint else []),
            metadata=metadata,
        )

    if wants_change:
        metadata = {"requires_verification": wants_verification or True}
        return GoalSpec(
            original_goal=user_input,
            primary_intent="change_and_verify",
            requires_file_read=bool(target_hint),
            requires_final_synthesis=True,
            target_paths=([target_hint] if target_hint else (["README.md"] if wants_readme else [])),
            metadata=metadata,
        )

    if wants_overview and wants_readme:
        return GoalSpec(
            original_goal=user_input,
            primary_intent="compound",
            requires_repository_overview=True,
            requires_file_read=True,
            requires_final_synthesis=True,
            target_paths=["README.md"],
        )

    if wants_readme:
        return GoalSpec(
            original_goal=user_input,
            primary_intent="file_read",
            requires_file_read=True,
            target_paths=["README.md"],
            metadata={"wants_summary": wants_summary},
        )

    if target_hint:
        return GoalSpec(
            original_goal=user_input,
            primary_intent="file_read",
            requires_file_read=True,
            target_paths=[target_hint],
            metadata={"target_hint": target_hint, "wants_summary": wants_summary},
        )

    if target_hint and wants_read:
        return GoalSpec(
            original_goal=user_input,
            primary_intent="file_read",
            requires_file_read=True,
            target_paths=[target_hint],
            metadata={"wants_summary": wants_summary or ("总结" in user_input), "target_hint": target_hint},
        )

    if wants_overview:
        return GoalSpec(
            original_goal=user_input,
            primary_intent="repository_overview",
            requires_repository_overview=True,
        )

    wants_target_explainer = (not target_hint) and any(
        token in user_input for token in ["讲解", "解释", "模块", "文件", "看看", "查看", "读取", "总结"]
    )
    if wants_target_explainer:
        metadata = {"target_query": user_input}
        if target_hint:
            metadata["target_hint"] = target_hint
        return GoalSpec(original_goal=user_input, primary_intent="target_explainer", metadata=metadata)

    return GoalSpec(original_goal=user_input, primary_intent="generic")
