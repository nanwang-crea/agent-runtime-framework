from __future__ import annotations

import json
import logging
import os
import re
import ssl
from typing import Any, Iterable
from urllib.error import URLError

from agent_runtime_framework.assistant.capabilities import CapabilitySpec
from agent_runtime_framework.agents.codex.prompting import extract_json_block, render_codex_prompt_doc
from agent_runtime_framework.agents.codex.run_context import build_run_context_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, chat_stream, resolve_model_runtime

logger = logging.getLogger(__name__)

_WORKSPACE_VERB_MARKERS = (
    "读取",
    "读一下",
    "打开",
    "查看",
    "列出",
    "列一下",
    "总结",
    "概括",
    "搜索",
    "查找",
    "创建",
    "新建",
    "编辑",
    "修改",
    "替换",
    "移动",
    "重命名",
    "删除",
    "运行",
    "测试",
    "验证",
    "read ",
    "open ",
    "list ",
    "summarize ",
    "search ",
    "create ",
    "edit ",
    "move ",
    "rename ",
    "delete ",
    "run ",
    "test ",
    "verify ",
)

_WORKSPACE_NOUN_MARKERS = (
    "工作区",
    "文件",
    "目录",
    "文件夹",
    "路径",
    "模块",
    "代码库",
    "仓库",
    "workspace",
    "file",
    "directory",
    "folder",
    "path",
    "package",
    "repo",
)

_RESOURCE_PATTERN = re.compile(r"(^|[\s\"'])[\w./-]+\.[A-Za-z0-9]{1,8}($|[\s\"'])")
_PATH_PATTERN = re.compile(r"(^|[\s\"'])(?:\.{1,2}/|/)[^\s]+")


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
    if "conversation" in registry.names():
        return "conversation"
    if "desktop_content" in registry.names():
        return "desktop_content"
    return None

def route_user_message(user_input: str, context: Any | None = None) -> str:
    return get_route_decision(user_input, context)["route"]


def get_route_decision(user_input: str, context: Any | None = None) -> dict[str, str]:
    model_route = _route_with_model(user_input, context)
    if model_route in {"conversation", "codex"}:
        return {"route": model_route, "source": "model"}
    return {"route": "codex", "source": "default"}


def should_route_to_conversation(user_input: str, context: Any | None = None) -> bool:
    return route_user_message(user_input, context) == "conversation"


def _route_with_model(user_input: str, context: Any | None) -> str | None:
    if context is None:
        return None
    application_context = getattr(context, "application_context", context)
    runtime = resolve_model_runtime(application_context, "router")
    if runtime is None:
        return None
    try:
        response = chat_once(
            runtime.client,
            ChatRequest(
                model=runtime.profile.model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=render_codex_prompt_doc("router_system"),
                    ),
                    ChatMessage(
                        role="user",
                        content=render_codex_prompt_doc("router_user", user_input=user_input),
                    ),
                ],
                temperature=0.0,
                max_tokens=120,
            ),
        )
    except Exception as exc:
        logger.warning("router request failed: %s: %s", type(exc).__name__, exc)
        return None
    raw_content = (response.content or "").strip()
    try:
        parsed = json.loads(extract_json_block(raw_content))
    except Exception:
        logger.warning("router invalid json: raw=%s", raw_content[:300])
        return None
    route = str(parsed.get("route") or "").strip().lower()
    if route not in {"conversation", "codex"}:
        logger.warning("router normalization failed: parsed=%s", json.dumps(parsed, ensure_ascii=False)[:300])
    return route if route in {"conversation", "codex"} else None


def _run_conversation(user_input: str, context: Any, session: Any) -> str:
    diagnostics: dict[str, str | None] = {"source": "fallback", "reason": "unknown"}
    final_answer = "".join(stream_conversation_reply(user_input, context, session, diagnostics=diagnostics))
    source = str(diagnostics.get("source") or "fallback")
    reason = str(diagnostics.get("reason") or "")
    status = "completed" if source == "model" else "fallback"
    return {
        "final_answer": final_answer,
        "execution_trace": [
            {
                "name": "conversation",
                "status": status,
                "detail": f"source={source}; reason={reason}" if reason else f"source={source}",
            }
        ],
    }


def stream_conversation_reply(
    user_input: str,
    context: Any,
    session: Any,
    *,
    diagnostics: dict[str, str | None] | None = None,
) -> Iterable[str]:
    meta = diagnostics if diagnostics is not None else {}
    meta["source"] = "fallback"
    meta["reason"] = "llm_unavailable"
    runtime = resolve_model_runtime(context.application_context, "conversation")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    max_tokens = _conversation_max_tokens()

    if llm_client is None:
        reason = (
            "llm_unavailable: 未配置可用模型。请在前端「模型 / 配置」中："
            "1) 配置一个可用的模型实例并完成认证；"
            "2) 为 conversation 绑定实例和模型。"
        )
        meta["reason"] = reason
        logger.warning("conversation fallback: %s", reason)

    if llm_client is not None:
        # 优先使用流式请求；成功则逐 chunk yield，并标记 source=model, reason=stream
        try:
            response = chat_stream(
                llm_client,
                ChatRequest(
                    model=model_name,
                    messages=_build_messages(user_input, session, context=context),
                    temperature=0.3,
                    max_tokens=max_tokens,
                ),
            )
            streamed = False
            for chunk in response:
                streamed = True
                if chunk.content:
                    yield chunk.content
            if streamed:
                meta["source"] = "model"
                meta["reason"] = "stream"
                return
            meta["reason"] = "empty_stream"
        except Exception as exc:
            # 流式请求失败（如网络/代理/接口不支持）时，再试一次非流式，尽量仍返回模型结果
            error_detail = _format_error_detail(exc)
            meta["reason"] = f"stream_error:{error_detail}"
            if _is_transient_network_error(exc):
                logger.warning("conversation stream transient failure: %s", error_detail)
            else:
                logger.exception("conversation stream request failed: %s", error_detail)
        try:
            response = chat_once(
                llm_client,
                ChatRequest(
                    model=model_name,
                    messages=_build_messages(user_input, session, context=context),
                    temperature=0.3,
                    max_tokens=max_tokens,
                ),
            )
            content = response.content or ""
            if content.strip():
                meta["source"] = "model"
                meta["reason"] = "non_stream_fallback"
                yield content.strip()
                return
        except Exception as exc2:
            error_detail = _format_error_detail(exc2)
            meta["reason"] = f"model_error:{error_detail}"
            if _is_transient_network_error(exc2):
                logger.warning("conversation non-stream transient failure: %s", error_detail)
            else:
                logger.exception("conversation non-stream request failed: %s", error_detail)
    yield _fallback_conversation_reply(user_input)


def _build_messages(user_input: str, session: Any, context: Any | None = None) -> list[ChatMessage]:
    system_content = render_codex_prompt_doc("conversation_system")
    if context is not None:
        system_content += "\n\n" + build_run_context_block(context, session=session, user_input=user_input)
    messages: list[ChatMessage] = [
        ChatMessage(
            role="system",
            content=system_content,
        )
    ]
    recent_turns = list(getattr(session, "turns", [])[-6:])
    if recent_turns:
        last_turn = recent_turns[-1]
        if getattr(last_turn, "role", None) == "user" and getattr(last_turn, "content", "") == user_input:
            recent_turns = recent_turns[:-1]
    for turn in recent_turns:
        messages.append(ChatMessage(role=turn.role, content=turn.content))
    messages.append(ChatMessage(role="user", content=user_input))
    return messages


def _fallback_conversation_reply(user_input: str) -> str:
    text = user_input.strip()
    if not text:
        return "你可以直接和我聊天，或者让我读取、列出、总结当前工作区里的文件。"
    lowered = text.lower()
    if any(token in text for token in ("你好", "嗨", "hello", "hi")):
        return "你好。我可以陪你对话，也可以帮你查看当前工作区里的文件、目录和内容。"
    if "流式" in text:
        return "当前接口支持流式事件；如果你在页面上还是看到整段一次性出现，通常说明前端渲染或模型回包链路还没有真正按增量消费。"
    if "代办" in text:
        return "可以。我能帮你整理代办、拆步骤，或者直接检查当前工作区里和任务相关的文件。"
    return "我可以继续和你对话，也可以按你的意图去查看工作区里的文件、目录或文档内容。"


def _format_error_detail(exc: Exception) -> str:
    detail = f"{type(exc).__name__}: {exc}".strip()
    detail = " ".join(detail.split())
    return detail[:240]


def _conversation_max_tokens() -> int:
    raw = os.getenv("ARF_CONVERSATION_MAX_TOKENS", "").strip()
    if not raw:
        return 10240
    try:
        value = int(raw)
    except ValueError:
        return 10240
    return min(10240, max(1, value))


def _is_transient_network_error(exc: Exception) -> bool:
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLEOFError):
            return True
        if isinstance(reason, ssl.SSLError):
            return "EOF" in str(reason).upper()
        return "EOF OCCURRED IN VIOLATION OF PROTOCOL" in str(reason).upper()
    if isinstance(exc, ssl.SSLEOFError):
        return True
    if isinstance(exc, ssl.SSLError):
        return "EOF" in str(exc).upper()
    return False
