from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.llm.access import get_application_context


DEFAULT_TEMPERATURE = 0.2
def synthesize_text(
    context: Any,
    *,
    role: str,
    system_prompt: str,
    payload: dict[str, Any],
    max_tokens: int,
    temperature: float = DEFAULT_TEMPERATURE,
) -> str | None:
    application_context = get_application_context(context)
    if application_context is None:
        return None

    runtime = resolve_model_runtime(application_context, role)
    if runtime is None:
        return None

    try:
        response = chat_once(
            runtime.client,
            ChatRequest(
                model=runtime.profile.model_name,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
    except Exception:
        return None

    content = str(response.content or "").strip()
    return content or None
