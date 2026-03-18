from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any
from urllib import request as urllib_request

from agent_runtime_framework.models.core import AuthSession, CredentialStore, ModelProfile


class _CompatibleChatCompletions:
    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def create(self, **kwargs):
        payload = json.dumps(
            {
                "model": kwargs["model"],
                "messages": kwargs["messages"],
                "temperature": kwargs.get("temperature", 0.0),
                "max_tokens": kwargs.get("max_tokens"),
                "stream": bool(kwargs.get("stream")),
            }
        ).encode("utf-8")
        req = urllib_request.Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        if kwargs.get("stream"):
            return _streaming_response_chunks(req)
        with urllib_request.urlopen(req, timeout=60) as response:
            parsed = json.loads(response.read().decode("utf-8"))
        content = (
            parsed.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return type(
            "ChatCompletionResponse",
            (),
            {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]},
        )()


class _CompatibleChat:
    def __init__(self, api_key: str, base_url: str) -> None:
        self.completions = _CompatibleChatCompletions(api_key, base_url)


class _CompatibleClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self.chat = _CompatibleChat(api_key, base_url)


def _streaming_response_chunks(req: Any):
    with urllib_request.urlopen(req, timeout=60) as response:
        yield from _iter_streaming_chunks(response)


def _iter_streaming_chunks(response: Any):
    for raw_line in response:
        line = raw_line.decode("utf-8").strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        parsed = json.loads(payload)
        content = (
            parsed.get("choices", [{}])[0]
            .get("delta", {})
            .get("content")
        )
        yield type(
            "ChatCompletionChunk",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {"delta": type("Delta", (), {"content": content})()},
                    )()
                ]
            },
        )()


@dataclass(slots=True)
class OpenAICompatibleProvider:
    provider_name: str = "openai"
    default_base_url: str | None = None
    available_models: list[ModelProfile] = field(
        default_factory=lambda: [
            ModelProfile(
                provider="openai",
                model_name="gpt-4.1-mini",
                display_name="GPT-4.1 Mini",
                cost_level="low",
                latency_level="low",
                reasoning_level="medium",
                recommended_roles=["conversation", "capability_selector", "composer"],
            ),
            ModelProfile(
                provider="openai",
                model_name="gpt-4.1",
                display_name="GPT-4.1",
                cost_level="medium",
                latency_level="medium",
                reasoning_level="high",
                recommended_roles=["planner", "reviewer", "conversation"],
            ),
        ]
    )

    def list_models(self) -> list[ModelProfile]:
        return [
            ModelProfile(**{**profile.as_dict(), "provider": self.provider_name})
            for profile in self.available_models
        ]

    def authenticate(self, credentials: dict[str, Any], store: CredentialStore) -> AuthSession:
        api_key = str(credentials.get("api_key") or "").strip()
        if not api_key:
            return AuthSession(
                provider=self.provider_name,
                authenticated=False,
                auth_type="api_key",
                error_message="missing api_key",
            )
        stored = {"api_key": api_key}
        base_url = str(credentials.get("base_url") or self.default_base_url or "").strip()
        if base_url:
            stored["base_url"] = base_url
        store.set(self.provider_name, stored)
        return AuthSession(
            provider=self.provider_name,
            authenticated=True,
            auth_type="api_key",
            metadata={"base_url": base_url} if base_url else {},
        )

    def get_client(self, store: CredentialStore) -> Any:
        credentials = store.get(self.provider_name) or {}
        api_key = str(credentials.get("api_key") or "").strip()
        if not api_key:
            return None
        base_url = str(credentials.get("base_url") or self.default_base_url or "").strip()
        if not base_url:
            return None
        return _CompatibleClient(api_key=api_key, base_url=base_url)
