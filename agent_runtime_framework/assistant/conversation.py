from __future__ import annotations

from typing import Any

from agent_runtime_framework.assistant.capabilities import CapabilitySpec
from agent_runtime_framework.models import resolve_model_runtime


def create_conversation_capability(name: str = "conversation") -> CapabilitySpec:
    return CapabilitySpec(
        name=name,
        runner=_run_conversation,
        source="assistant",
        description="General conversation and question answering capability.",
        safety_level="chat",
        cost_hint="medium",
        latency_hint="medium",
        risk_class="low",
        dependency_readiness="ready",
        output_type="chat_message",
    )


def route_default_capability(user_input: str, _session: Any, registry: Any, _context: Any) -> str | None:
    lowered = user_input.strip().lower()
    desktop_markers = (
        "读取",
        "列出",
        "总结",
        "打开",
        "查看",
        "目录",
        "文件",
        "read ",
        "list ",
        "summarize ",
        ".md",
        ".txt",
        ".py",
        "/",
    )
    if any(marker in lowered for marker in desktop_markers) and "desktop_content" in registry.names():
        return "desktop_content"
    if "conversation" in registry.names():
        return "conversation"
    return None


def _run_conversation(user_input: str, context: Any, session: Any) -> str:
    runtime = resolve_model_runtime(context.application_context, "conversation")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is not None and hasattr(llm_client, "chat"):
        try:
            response = llm_client.chat.completions.create(
                model=model_name,
                messages=_build_messages(user_input, session),
                temperature=0.3,
                max_tokens=400,
            )
            content = response.choices[0].message.content or ""
            if content.strip():
                return content.strip()
        except Exception:
            pass
    return _fallback_conversation_reply(user_input)


def _build_messages(user_input: str, session: Any) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是一个桌面 AI 助手。"
                "当用户是在正常聊天、提问或讨论方案时，直接自然回答。"
                "当用户明确要求操作本地文件时，应由其他 capability 处理。"
            ),
        }
    ]
    for turn in getattr(session, "turns", [])[-6:]:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": user_input})
    return messages


def _fallback_conversation_reply(user_input: str) -> str:
    text = user_input.strip()
    if not text:
        return "你可以直接和我聊天，或者让我读取、列出、总结当前工作区里的文件。"
    return (
        "我现在已经支持正常对话，也可以帮你处理当前工作区里的文件和目录。"
        f"你刚才说的是：{text}"
    )
