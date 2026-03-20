from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(slots=True)
class ChatResponse:
    content: str
    raw: Any = None


@dataclass(slots=True)
class ChatChunk:
    content: str
    raw: Any = None


class StandardChatClient:
    def create_chat_completion(self, request: ChatRequest) -> ChatResponse: ...

    def stream_chat_completion(self, request: ChatRequest) -> Iterable[ChatChunk]: ...


def chat_once(client: Any, request: ChatRequest) -> ChatResponse:
    if client is None:
        raise RuntimeError("missing llm client")
    if hasattr(client, "create_chat_completion"):
        return client.create_chat_completion(request)
    if hasattr(client, "chat") and hasattr(client.chat, "completions"):
        response = client.chat.completions.create(
            model=request.model,
            messages=[message.as_dict() for message in request.messages],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        content = response.choices[0].message.content or ""
        return ChatResponse(content=str(content), raw=response)
    raise RuntimeError("client does not support chat completion")


def chat_stream(client: Any, request: ChatRequest) -> Iterable[ChatChunk]:
    if client is None:
        raise RuntimeError("missing llm client")
    if hasattr(client, "stream_chat_completion"):
        yield from client.stream_chat_completion(request)
        return
    if hasattr(client, "chat") and hasattr(client.chat, "completions"):
        response = client.chat.completions.create(
            model=request.model,
            messages=[message.as_dict() for message in request.messages],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
        )
        for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            if content:
                yield ChatChunk(content=str(content), raw=chunk)
        return
    raise RuntimeError("client does not support streaming chat completion")
