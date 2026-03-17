from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.conversation import create_conversation_capability
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.models import (
    AuthSession,
    InMemoryCredentialStore,
    ModelProfile,
    ModelRegistry,
    ModelRouter,
    ModelRuntime,
    OpenAICompatibleProvider,
    ModelProvider,
    resolve_model_runtime,
)
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _FakeLLMClient:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


@dataclass
class _FakeProvider:
    provider_name: str = "fake"

    def __post_init__(self) -> None:
        self._profiles = [
            ModelProfile(
                provider=self.provider_name,
                model_name="fast-model",
                display_name="Fast Model",
                supports_chat=True,
                cost_level="low",
                latency_level="low",
                reasoning_level="medium",
                recommended_roles=["conversation", "capability_selector"],
            ),
            ModelProfile(
                provider=self.provider_name,
                model_name="planner-model",
                display_name="Planner Model",
                supports_chat=True,
                cost_level="medium",
                latency_level="medium",
                reasoning_level="high",
                recommended_roles=["planner"],
            ),
        ]

    def list_models(self) -> list[ModelProfile]:
        return list(self._profiles)

    def authenticate(self, credentials: dict[str, str], store: InMemoryCredentialStore) -> AuthSession:
        api_key = credentials.get("api_key", "")
        authenticated = bool(api_key)
        if authenticated:
            store.set(self.provider_name, {"api_key": api_key})
        return AuthSession(
            provider=self.provider_name,
            authenticated=authenticated,
            auth_type="api_key",
            error_message=None if authenticated else "missing api_key",
        )

    def get_client(self, store: InMemoryCredentialStore):
        stored = store.get(self.provider_name) or {}
        if not stored.get("api_key"):
            return None
        return _FakeLLMClient('{"capability_name":"conversation"}')


def _app_context(workspace: Path) -> ApplicationContext:
    return ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace)},
    )


def test_model_registry_authenticates_provider_and_lists_models():
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_provider(_FakeProvider())

    auth = registry.authenticate("fake", {"api_key": "secret"})

    assert auth.authenticated is True
    assert [profile.model_name for profile in registry.list_models()] == ["fast-model", "planner-model"]


def test_model_router_returns_runtime_for_selected_role():
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_provider(_FakeProvider())
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("conversation", provider="fake", model_name="fast-model")

    runtime = router.resolve("conversation")

    assert runtime is not None
    assert runtime.profile.model_name == "fast-model"
    assert runtime.client is not None


def test_resolve_model_runtime_prefers_router_selection(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _app_context(workspace)
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_provider(_FakeProvider())
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("conversation", provider="fake", model_name="fast-model")
    context.services["model_registry"] = registry
    context.services["model_router"] = router

    runtime = resolve_model_runtime(context, "conversation")

    assert runtime is not None
    assert runtime.profile.model_name == "fast-model"


def test_conversation_capability_uses_model_router_when_available(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _app_context(workspace)
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_provider(_FakeProvider())
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("conversation", provider="fake", model_name="fast-model")
    context.services["model_registry"] = registry
    context.services["model_router"] = router
    capability = create_conversation_capability()

    result = capability.runner("你好", SimpleNamespace(application_context=context), SimpleNamespace(turns=[]))

    assert result == '{"capability_name":"conversation"}'


def test_openai_compatible_provider_uses_pure_python_http_client():
    store = InMemoryCredentialStore()
    provider = OpenAICompatibleProvider(
        provider_name="dashscope",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    provider.authenticate(
        {
            "api_key": "sk-test",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
        store,
    )
    client = provider.get_client(store)

    class _FakeHTTPResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"hello from http"}}]}'

    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse()) as mocked:
        result = client.chat.completions.create(
            model="qwen3.5-plus",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.3,
            max_tokens=100,
        )

    assert mocked.called
    assert result.choices[0].message.content == "hello from http"


def test_conversation_capability_falls_back_when_model_request_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _app_context(workspace)

    class _RaisingCompletions:
        def create(self, **kwargs):
            raise RuntimeError("network down")

    context.llm_client = SimpleNamespace(chat=SimpleNamespace(completions=_RaisingCompletions()))
    capability = create_conversation_capability()

    result = capability.runner("你是谁？", SimpleNamespace(application_context=context), SimpleNamespace(turns=[]))

    assert "我现在已经支持正常对话" in result
