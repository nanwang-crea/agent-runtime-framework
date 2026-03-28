from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import build_codex_system_prompt, build_follow_up_context
from agent_runtime_framework.agents.codex.run_context import build_run_context_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime


def classify_task_profile(user_input: str, context: Any | None = None, session: Any | None = None) -> str:
    llm_profile = _classify_task_profile_with_model(user_input, context, session=session)
    if llm_profile is not None:
        return llm_profile
    return "chat"


def extract_workspace_target_hint(user_input: str) -> str:
    """Return empty string; target extraction is delegated to the LLM planner."""
    return ""


def is_list_only_request(goal: str) -> bool:
    """Deprecated: always returns False; list-vs-deep distinction handled by LLM."""
    return False


def _classify_task_profile_with_model(user_input: str, context: Any | None = None, *, session: Any | None = None) -> str | None:
    if context is None:
        return None
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        return None
    try:
        persona = resolve_runtime_persona(context, user_input=user_input)
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=build_codex_system_prompt(
                            "You are a task-profile classifier. Output only JSON in the format: "
                            '{"profile":"chat|repository_explainer|file_reader|change_and_verify|debug_and_fix|multi_file_change|test_and_verify"}. '
                            "Choose exactly one profile from the enum above.",
                            persona=persona,
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"User request: {user_input}\n"
                            + (build_run_context_block(context, session=session, user_input=user_input, persona=persona) + "\n" if context is not None else "")
                            + "Profile selection rules:\n"
                            "- repository_explainer: listing files/directories, exploring workspace/repo structure, asking what files exist or what a directory contains.\n"
                            "- file_reader: reading, summarizing, or explaining a specific file's content.\n"
                            "- debug_and_fix: error, bug, crash, exception, debugging, or fix request.\n"
                            "- multi_file_change: refactoring, batch edits, updating all call sites, renaming across files.\n"
                            "- change_and_verify: editing a file, applying a patch, creating/deleting/moving a file, or a single-file change.\n"
                            "- test_and_verify: running tests, checking test results, fixing test failures.\n"
                            "- chat: general question, explanation, or anything that does not require workspace changes or file access.\n"
                            "Output only JSON."
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=64,
            ),
        )
    except Exception:
        return None
    raw = (response.content or "").strip()
    if "```" in raw:
        raw = raw.split("```", 1)[-1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]
    try:
        parsed = json.loads(raw.strip())
    except Exception:
        return None
    profile = str(parsed.get("profile") or "").strip()
    return profile if profile in {"chat", "repository_explainer", "file_reader", "change_and_verify", "debug_and_fix", "multi_file_change", "test_and_verify"} else None
