from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.agents.workspace_backend.prompting import extract_json_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime


def get_application_context(context: Any) -> Any | None:
    if isinstance(context, dict):
        return context.get("application_context")
    return getattr(context, "application_context", None)


def get_workspace_context(context: Any) -> Any | None:
    if isinstance(context, dict):
        return context.get("workspace_context")
    return getattr(context, "workspace_context", None)


def get_workspace_root(context: Any, default: str = ".") -> str:
    if isinstance(context, dict):
        return str(context.get("workspace_root", default))
    return str(getattr(context, "workspace_root", default))


def resolve_workflow_model_runtime(context: Any, role: str):
    application_context = get_application_context(context)
    if application_context is None:
        return None
    return resolve_model_runtime(application_context, role)


def chat_json(
    context: Any,
    *,
    role: str,
    system_prompt: str,
    payload: Any,
    max_tokens: int,
    temperature: float = 0.0,
) -> dict[str, Any] | None:
    application_context = get_application_context(context)
    if application_context is None:
        return None

    runtime = resolve_workflow_model_runtime(context, role)
    llm_client = runtime.client if runtime is not None else getattr(application_context, "llm_client", None)
    model_name = runtime.profile.model_name if runtime is not None else getattr(application_context, "llm_model", "")
    if llm_client is None or not model_name:
        return None

    request_payload = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    response = chat_once(
        llm_client,
        ChatRequest(
            model=model_name,
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=request_payload),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        ),
    )
    return json.loads(extract_json_block(str(response.content or "")))
