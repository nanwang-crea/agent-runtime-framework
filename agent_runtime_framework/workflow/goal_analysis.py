from __future__ import annotations

import json
import re
from typing import Any

from agent_runtime_framework.agents.workspace_backend.prompting import extract_json_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.models import GoalSpec


README_PATTERN = re.compile(r"readme(?:\.md)?", re.IGNORECASE)


def analyze_goal(user_input: str, context: Any | None = None) -> GoalSpec:
    text = user_input.strip()
    if not text:
        return GoalSpec(original_goal=text, primary_intent="generic")

    llm_goal = _analyze_goal_with_model(text, context=context)
    if llm_goal is not None:
        return llm_goal
    return _analyze_goal_with_keywords(text)


def _analyze_goal_with_model(user_input: str, *, context: Any | None) -> GoalSpec | None:
    if context is None:
        return None
    if not bool(getattr(context, "services", {}).get("model_first_workflow_goal_analysis")):
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
                            "You analyze a user goal for a workflow runtime. "
                            "Return JSON only with keys: primary_intent, requires_repository_overview, "
                            "requires_file_read, requires_final_synthesis, target_paths, metadata."
                        ),
                    ),
                    ChatMessage(role="user", content=user_input),
                ],
                temperature=0.0,
                max_tokens=300,
            ),
        )
    except Exception:
        return None

    try:
        parsed = json.loads(extract_json_block(str(response.content or "")))
    except Exception:
        return None

    primary_intent = str(parsed.get("primary_intent") or "").strip()
    if not primary_intent:
        return None
    target_paths = [str(item).strip() for item in parsed.get("target_paths") or [] if str(item).strip()]
    return GoalSpec(
        original_goal=user_input,
        primary_intent=primary_intent,
        requires_repository_overview=bool(parsed.get("requires_repository_overview")),
        requires_file_read=bool(parsed.get("requires_file_read")),
        requires_final_synthesis=bool(parsed.get("requires_final_synthesis")),
        target_paths=target_paths,
        metadata=dict(parsed.get("metadata") or {}),
    )


def _analyze_goal_with_keywords(user_input: str) -> GoalSpec:
    wants_readme = bool(README_PATTERN.search(user_input))
    wants_overview = any(token in user_input for token in ["列一下", "文件夹", "目录", "仓库结构", "当前文件夹", "仓库"])
    wants_summary = any(token in user_input for token in ["总结", "概括", "讲什么", "介绍"])

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

    if wants_overview:
        return GoalSpec(
            original_goal=user_input,
            primary_intent="repository_overview",
            requires_repository_overview=True,
        )

    return GoalSpec(original_goal=user_input, primary_intent="generic")
