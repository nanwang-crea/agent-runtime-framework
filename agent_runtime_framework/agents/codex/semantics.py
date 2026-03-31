from __future__ import annotations

import json
from pathlib import Path

from agent_runtime_framework.agents.codex.models import TaskIntent
from agent_runtime_framework.agents.codex.semantics_rules import (
    goal_is_raw_read,
    goal_prefers_summary,
    infer_task_intent_from_keywords,
    repository_target_hint,
)
from agent_runtime_framework.agents.codex.semantics_utils import (
    extract_json_block,
    load_prompt,
    recent_turns_block,
    workspace_candidates_block,
    workspace_root_from_context,
)
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime


def infer_task_intent(
    user_input: str,
    workspace_root: Path | None = None,
    context: object | None = None,
    session: object | None = None,
) -> TaskIntent:
    text = str(user_input or "").strip()
    if not text:
        return TaskIntent(suggested_tool_chain=[])
    llm_intent = _infer_task_intent_with_model(text, workspace_root=workspace_root, context=context, session=session)
    if llm_intent is not None:
        return llm_intent
    fallback = infer_task_intent_from_keywords(text, workspace_root)
    if fallback.task_kind:
        return fallback
    return TaskIntent(task_kind="chat", user_intent="general_chat", goal_mode="direct_answer", expected_output="direct_answer", suggested_tool_chain=["respond"])


def resolve_task_intent(
    user_input: str,
    context: object | None = None,
    *,
    session: object | None = None,
) -> TaskIntent:
    workspace_root = workspace_root_from_context(context)
    return infer_task_intent(user_input, workspace_root, context=context, session=session)


def _infer_task_intent_with_model(
    user_input: str,
    *,
    workspace_root: Path | None,
    context: object | None,
    session: object | None,
) -> TaskIntent | None:
    if context is None:
        return None
    if not bool(getattr(context, "services", {}).get("model_first_task_intent")):
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
                    ChatMessage(role="system", content=_load_prompt("task_intent_system")),
                    ChatMessage(role="system", content=load_prompt("task_intent_system")),
                    ChatMessage(
                        role="user",
                        content=load_prompt("task_intent_user")
                        .replace("{{user_input}}", user_input)
                        .replace("{{workspace_root}}", str(workspace_root or ""))
                        .replace("{{recent_turns}}", recent_turns_block(session))
                        .replace("{{workspace_candidates}}", workspace_candidates_block(workspace_root)),
                    ),
                ],
                temperature=0.0,
                max_tokens=220,
            ),
        )
    except Exception:
        return None
    try:
        parsed = json.loads(extract_json_block(str(response.content or "")))
    except Exception:
        return None
    task_kind = str(parsed.get("task_kind") or "").strip()
    if task_kind not in {
        "chat",
        "repository_explainer",
        "file_reader",
        "change_and_verify",
        "debug_and_fix",
        "multi_file_change",
        "test_and_verify",
    }:
        return None
    return TaskIntent(
        task_kind=task_kind,
        user_intent=str(parsed.get("user_intent") or "general_chat").strip() or "general_chat",
        goal_mode=str(parsed.get("goal_mode") or parsed.get("expected_output") or "direct_answer").strip() or "direct_answer",
        scope_kind=str(parsed.get("scope_kind") or "unknown").strip() or "unknown",
        target_ref=str(parsed.get("target_ref") or parsed.get("target_hint") or "").strip(),
        target_hint=str(parsed.get("target_hint") or "").strip(),
        target_type=str(parsed.get("target_type") or "unknown").strip() or "unknown",
        target_confidence=float(parsed.get("target_confidence") or parsed.get("confidence") or 0.0),
        expected_output=str(parsed.get("expected_output") or "direct_answer").strip() or "direct_answer",
        needs_clarification=bool(parsed.get("needs_clarification")),
        needs_grounding=bool(parsed.get("needs_grounding")),
        allowed_strategy_family=[str(item).strip() for item in parsed.get("allowed_strategy_family") or [] if str(item).strip()],
        suggested_tool_chain=[str(item).strip() for item in parsed.get("suggested_tool_chain") or [] if str(item).strip()],
        confidence=float(parsed.get("confidence") or 0.0),
    )
def build_task_intent_block(goal: str, workspace_root: Path | None = None) -> str:
    intent = infer_task_intent(goal, workspace_root)
    tool_chain = ", ".join(intent.suggested_tool_chain or []) or "(none)"
    return (
        "Task intent:\n"
        f"- task_kind: {intent.task_kind}\n"
        f"- user_intent: {intent.user_intent}\n"
        f"- target_hint: {intent.target_hint or '(unknown)'}\n"
        f"- target_type: {intent.target_type}\n"
        f"- expected_output: {intent.expected_output}\n"
        f"- needs_grounding: {str(intent.needs_grounding).lower()}\n"
        f"- suggested_tool_chain: {tool_chain}\n"
        f"- confidence: {intent.confidence:.2f}"
    )
