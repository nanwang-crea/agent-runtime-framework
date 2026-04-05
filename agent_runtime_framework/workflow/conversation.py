from __future__ import annotations

from typing import Any

from agent_runtime_framework.models import ChatMessage
from agent_runtime_framework.workflow.prompting import build_run_context_block, render_workflow_prompt_doc


def build_conversation_messages(user_input: str, session: Any, context: Any | None = None) -> list[ChatMessage]:
    system_content = render_workflow_prompt_doc("conversation_system")
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
