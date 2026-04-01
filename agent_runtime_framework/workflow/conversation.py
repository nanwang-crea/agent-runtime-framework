from __future__ import annotations

from typing import Any

from agent_runtime_framework.agents.workspace_backend.prompting import render_workspace_prompt_doc
from agent_runtime_framework.agents.workspace_backend.run_context import build_run_context_block
from agent_runtime_framework.models import ChatMessage


def build_conversation_messages(user_input: str, session: Any, context: Any | None = None) -> list[ChatMessage]:
    system_content = render_workspace_prompt_doc("conversation_system")
    if context is not None:
        system_content += "\n\n" + build_run_context_block(context, session=session, user_input=user_input)
    messages = [ChatMessage(role="system", content=system_content)]
    recent_turns = list(getattr(session, "turns", [])[-6:]) if session is not None else []
    if recent_turns:
        last_turn = recent_turns[-1]
        if getattr(last_turn, "role", None) == "user" and getattr(last_turn, "content", "") == user_input:
            recent_turns = recent_turns[:-1]
    for turn in recent_turns:
        messages.append(ChatMessage(role=turn.role, content=turn.content))
    messages.append(ChatMessage(role="user", content=user_input))
    return messages
