from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterable
from urllib import request as urllib_request

from agent_runtime_framework.models.chat import ChatChunk, ChatMessage, ChatRequest, ChatResponse
from agent_runtime_framework.models.core import AuthSession, CredentialStore, DriverCapabilities, ModelDriver, ModelInstance, ModelProfile


class _OpenAICompatibleClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.chat = _OpenAICompatChat(self)

    def create_chat_completion(self, request: ChatRequest) -> ChatResponse:
        parsed = self._send(request, stream=False)
        content = parsed.get("choices", [{}])[0].get("message", {}).get("content", "")
        return ChatResponse(content=str(content or ""), raw=parsed)

    def stream_chat_completion(self, request: ChatRequest) -> Iterable[ChatChunk]:
        req = self._request(request, stream=True)
        with urllib_request.urlopen(req, timeout=60) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                parsed = json.loads(payload)
                content = parsed.get("choices", [{}])[0].get("delta", {}).get("content")
                if content:
                    yield ChatChunk(content=str(content), raw=parsed)

    def _send(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        req = self._request(request, stream=stream)
        with urllib_request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request(self, request: ChatRequest, *, stream: bool) -> Any:
        payload = json.dumps(
            {
                "model": request.model,
                "messages": [message.as_dict() for message in request.messages],
                "temperature": request.temperature if request.temperature is not None else 0.0,
                "max_tokens": request.max_tokens,
                "stream": stream,
            }
        ).encode("utf-8")
        return urllib_request.Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )


class _OpenAICompatChatCompletions:
    def __init__(self, client: _OpenAICompatibleClient) -> None:
        self._client = client

    def create(self, **kwargs):
        request = ChatRequest(
            model=str(kwargs["model"]),
            messages=[
                ChatMessage(
                    role=str(item.get("role") or ""),
                    content=str(item.get("content") or ""),
                )
                for item in kwargs.get("messages") or []
            ],
        )
        request.temperature = kwargs.get("temperature")
        request.max_tokens = kwargs.get("max_tokens")
        if kwargs.get("stream"):
            return _compat_stream(self._client.stream_chat_completion(request))
        response = self._client.create_chat_completion(request)
        return _compat_response(response.content)


class _OpenAICompatChat:
    def __init__(self, client: _OpenAICompatibleClient) -> None:
        self.completions = _OpenAICompatChatCompletions(client)


def _compat_response(content: str):
    return type(
        "ChatCompletionResponse",
        (),
        {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]},
    )()


def _compat_stream(chunks: Iterable[ChatChunk]):
    for chunk in chunks:
        yield type(
            "ChatCompletionChunk",
            (),
            {"choices": [type("Choice", (), {"delta": type("Delta", (), {"content": chunk.content})()})()]},
        )()


@dataclass(slots=True)
class OpenAICompatibleInstance(ModelInstance):
    instance_id: str
    default_base_url: str | None = None
    available_models: list[ModelProfile] = field(default_factory=list)

    def list_models(self) -> list[ModelProfile]:
        return [ModelProfile(**{**profile.as_dict(), "instance": self.instance_id}) for profile in self.available_models]

    def authenticate(self, credentials: dict[str, Any], store: CredentialStore) -> AuthSession:
        api_key = str(credentials.get("api_key") or "").strip()
        if not api_key:
            return AuthSession(
                instance=self.instance_id,
                authenticated=False,
                auth_type="api_key",
                error_message="missing api_key",
            )
        stored = {"api_key": api_key}
        base_url = str(credentials.get("base_url") or self.default_base_url or "").strip()
        if base_url:
            stored["base_url"] = base_url
        store.set(self.instance_id, stored)
        return AuthSession(
            instance=self.instance_id,
            authenticated=True,
            auth_type="api_key",
            metadata={"base_url": base_url} if base_url else {},
        )

    def get_client(self, store: CredentialStore) -> Any:
        credentials = store.get(self.instance_id) or {}
        api_key = str(credentials.get("api_key") or "").strip()
        if not api_key:
            return None
        base_url = str(credentials.get("base_url") or self.default_base_url or "").strip()
        if not base_url:
            return None
        return _OpenAICompatibleClient(api_key=api_key, base_url=base_url)


@dataclass(slots=True)
class OpenAICompatibleDriver(ModelDriver):
    driver_type: str = "openai_compatible"
    capabilities: DriverCapabilities = field(default_factory=lambda: DriverCapabilities(
        supports_stream=True,
        supports_tools=False,
        supports_vision=False,
        supports_json_mode=False,
    ))

    def create_instance(self, instance_id: str, config: dict[str, Any]) -> OpenAICompatibleInstance:
        connection = dict(config.get("connection") or {})
        catalog = dict(config.get("catalog") or {})
        model_names = [str(item).strip() for item in list(catalog.get("models") or []) if str(item).strip()]
        return OpenAICompatibleInstance(
            instance_id=instance_id,
            default_base_url=str(connection.get("base_url") or "").strip() or None,
            available_models=[_profile_for_model(instance_id, model_name) for model_name in model_names],
        )


def _profile_for_model(instance_id: str, model_name: str) -> ModelProfile:
    known = {
        "qwen3.5-plus": ("Qwen 3.5 Plus", "medium", "medium", "high"),
        "qwen-plus": ("Qwen Plus", "low", "low", "medium"),
        "MiniMax-M2.1": ("MiniMax-M2.1", "medium", "medium", "high"),
        "gpt-4.1-mini": ("GPT-4.1 Mini", "low", "low", "medium"),
        "gpt-4.1": ("GPT-4.1", "medium", "medium", "high"),
    }
    display_name, cost_level, latency_level, reasoning_level = known.get(
        model_name,
        (model_name, "medium", "medium", "medium"),
    )
    return ModelProfile(
        instance=instance_id,
        model_name=model_name,
        display_name=display_name,
        cost_level=cost_level,
        latency_level=latency_level,
        reasoning_level=reasoning_level,
        recommended_roles=["conversation", "capability_selector", "planner"],
    )
