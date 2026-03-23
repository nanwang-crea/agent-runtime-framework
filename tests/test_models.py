from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import ssl
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.conversation import create_conversation_capability, should_route_to_conversation
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.models import (
    AuthSession,
    ChatMessage,
    ChatRequest,
    CodexCliDriver,
    DriverCapabilities,
    InMemoryCredentialStore,
    ModelProfile,
    ModelRegistry,
    ModelRouter,
    ModelRuntime,
    OpenAICompatibleDriver,
    chat_once,
    chat_stream,
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
class _FakeInstance:
    instance_id: str = "fake"
    client_content: str = '{"capability_name":"conversation"}'
    last_client: _FakeLLMClient | None = None

    def __post_init__(self) -> None:
        self._profiles = [
            ModelProfile(
                instance=self.instance_id,
                model_name="fast-model",
                display_name="Fast Model",
                supports_chat=True,
                cost_level="low",
                latency_level="low",
                reasoning_level="medium",
                recommended_roles=["conversation", "capability_selector"],
            ),
            ModelProfile(
                instance=self.instance_id,
                model_name="planner-model",
                display_name="Planner Model",
                supports_chat=True,
                cost_level="medium",
                latency_level="medium",
                reasoning_level="high",
                recommended_roles=["planner"],
            ),
            ModelProfile(
                instance=self.instance_id,
                model_name="router-model",
                display_name="Router Model",
                supports_chat=True,
                cost_level="low",
                latency_level="low",
                reasoning_level="medium",
                recommended_roles=["router"],
            ),
        ]

    def list_models(self) -> list[ModelProfile]:
        return list(self._profiles)

    def authenticate(self, credentials: dict[str, str], store: InMemoryCredentialStore) -> AuthSession:
        api_key = credentials.get("api_key", "")
        authenticated = bool(api_key)
        if authenticated:
            store.set(self.instance_id, {"api_key": api_key})
        return AuthSession(
            instance=self.instance_id,
            authenticated=authenticated,
            auth_type="api_key",
            error_message=None if authenticated else "missing api_key",
        )

    def get_client(self, store: InMemoryCredentialStore):
        stored = store.get(self.instance_id) or {}
        if not stored.get("api_key"):
            return None
        self.last_client = _FakeLLMClient(self.client_content)
        return self.last_client


@dataclass
class _FakeDriver:
    driver_type: str = "fake"
    capabilities: DriverCapabilities = field(default_factory=DriverCapabilities)

    def create_instance(self, instance_id: str, config: dict[str, str]):
        instance = _FakeInstance(instance_id=instance_id)
        if config.get("api_key"):
            store = InMemoryCredentialStore()
            instance.authenticate(config, store)
        return instance


def _app_context(workspace: Path) -> ApplicationContext:
    return ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace)},
    )


def test_model_registry_authenticates_instance_and_lists_models():
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_instance(_FakeInstance())

    auth = registry.authenticate("fake", {"api_key": "secret"})

    assert auth.authenticated is True
    assert [profile.model_name for profile in registry.list_models()] == ["fast-model", "planner-model", "router-model"]


def test_model_router_returns_runtime_for_selected_role():
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_instance(_FakeInstance())
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("conversation", instance_id="fake", model_name="fast-model")

    runtime = router.resolve("conversation")

    assert runtime is not None
    assert runtime.profile.model_name == "fast-model"
    assert runtime.client is not None


def test_resolve_model_runtime_prefers_router_selection(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _app_context(workspace)
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_instance(_FakeInstance())
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("conversation", instance_id="fake", model_name="fast-model")
    context.services["model_registry"] = registry
    context.services["model_router"] = router

    runtime = resolve_model_runtime(context, "conversation")

    assert runtime is not None
    assert runtime.profile.model_name == "fast-model"


def test_resolve_model_runtime_falls_back_to_default_route(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _app_context(workspace)
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_instance(_FakeInstance())
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("default", instance_id="fake", model_name="fast-model")
    context.services["model_registry"] = registry
    context.services["model_router"] = router

    runtime = resolve_model_runtime(context, "resolver")

    assert runtime is not None
    assert runtime.profile.model_name == "fast-model"


def test_conversation_capability_uses_model_router_when_available(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _app_context(workspace)
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_instance(_FakeInstance())
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("conversation", instance_id="fake", model_name="fast-model")
    context.services["model_registry"] = registry
    context.services["model_router"] = router
    capability = create_conversation_capability()

    result = capability.runner("你好", SimpleNamespace(application_context=context), SimpleNamespace(turns=[]))

    assert result["final_answer"] == '{"capability_name":"conversation"}'
    assert "source=model" in str(result["execution_trace"][-1]["detail"])


def test_should_route_to_conversation_prefers_router_model_when_available(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _app_context(workspace)
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    instance = _FakeInstance(client_content='{"route":"conversation"}')
    registry.register_instance(instance)
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("router", instance_id="fake", model_name="router-model")
    context.services["model_registry"] = registry
    context.services["model_router"] = router

    routed = should_route_to_conversation("读取 README.md", SimpleNamespace(application_context=context))

    assert routed is True
    prompt = "\n".join(message["content"] for message in instance.last_client.completions.last_kwargs["messages"])
    assert '{"route":"conversation"}' in prompt
    assert '{"route":"codex"}' in prompt
    assert "不要输出原因" in prompt


def test_should_route_to_conversation_falls_back_when_router_output_is_invalid(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _app_context(workspace)
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_instance(_FakeInstance(client_content="not-json"))
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("router", instance_id="fake", model_name="router-model")
    context.services["model_registry"] = registry
    context.services["model_router"] = router

    routed = should_route_to_conversation("读取 README.md", SimpleNamespace(application_context=context))

    assert routed is False


def test_openai_compatible_driver_builds_instance_with_pure_python_http_client():
    store = InMemoryCredentialStore()
    driver = OpenAICompatibleDriver()
    instance = driver.create_instance(
        "dashscope",
        {
            "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
            "catalog": {"models": ["qwen3.5-plus"]},
        },
    )
    instance.authenticate(
        {
            "api_key": "sk-test",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
        store,
    )
    client = instance.get_client(store)

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


def test_openai_compatible_driver_retries_transient_ssl_eof_once():
    store = InMemoryCredentialStore()
    driver = OpenAICompatibleDriver()
    instance = driver.create_instance(
        "openai",
        {
            "connection": {"base_url": "https://api.openai.com/v1"},
            "catalog": {"models": ["gpt-5.4"]},
        },
    )
    instance.authenticate(
        {
            "api_key": "sk-test",
            "base_url": "https://api.openai.com/v1",
        },
        store,
    )
    client = instance.get_client(store)

    class _FakeHTTPResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"retry-ok"}}]}'

    error = URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol"))
    with patch("urllib.request.urlopen", side_effect=[error, _FakeHTTPResponse()]) as mocked:
        response = client.create_chat_completion(
            ChatRequest(model="gpt-5.4", messages=[ChatMessage(role="user", content="hi")])
        )

    assert mocked.call_count == 2
    assert response.content == "retry-ok"


def test_chat_once_uses_standardized_response_contract():
    class _Client:
        def create_chat_completion(self, request: ChatRequest):
            assert request.model == "demo-model"
            assert request.messages[0].content == "hi"
            return type("Resp", (), {"content": "ok"})()

    response = chat_once(
        _Client(),
        ChatRequest(model="demo-model", messages=[ChatMessage(role="user", content="hi")]),
    )

    assert response.content == "ok"


def test_openai_compatible_driver_instance_keeps_stream_response_open_while_iterating():
    store = InMemoryCredentialStore()
    driver = OpenAICompatibleDriver()
    instance = driver.create_instance(
        "dashscope",
        {
            "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
            "catalog": {"models": ["qwen3.5-plus"]},
        },
    )
    instance.authenticate(
        {
            "api_key": "sk-test",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
        store,
    )
    client = instance.get_client(store)

    class _StreamingHTTPResponse:
        def __init__(self) -> None:
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.closed = True
            return False

        def __iter__(self):
            if self.closed:
                raise RuntimeError("response closed before stream consumption")
            return iter(
                [
                    b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
                    b"data: [DONE]\n\n",
                ]
            )

    with patch("urllib.request.urlopen", return_value=_StreamingHTTPResponse()):
        response = client.chat.completions.create(
            model="qwen3.5-plus",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.3,
            max_tokens=100,
            stream=True,
        )
        chunks = list(response)

    assert [chunk.choices[0].delta.content for chunk in chunks] == ["hello"]


def test_chat_stream_uses_standardized_chunk_contract():
    class _Client:
        def stream_chat_completion(self, request: ChatRequest):
            assert request.model == "demo-model"
            yield type("Chunk", (), {"content": "a"})()
            yield type("Chunk", (), {"content": "b"})()

    chunks = list(
        chat_stream(
            _Client(),
            ChatRequest(model="demo-model", messages=[ChatMessage(role="user", content="hi")]),
        )
    )

    assert [chunk.content for chunk in chunks] == ["a", "b"]


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

    assert "我可以继续和你对话" in result["final_answer"]
    assert "source=fallback" in str(result["execution_trace"][-1]["detail"])


def test_codex_cli_driver_instance_authenticates_with_local_auth_file(tmp_path: Path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "token", "refresh_token": "refresh"},
                "last_refresh": "2026-03-19T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    store = InMemoryCredentialStore()
    driver = CodexCliDriver()
    instance = driver.create_instance(
        "codex_local",
        {
            "connection": {"auth_file": str(auth_file), "codex_binary": "codex"},
            "catalog": {"models": ["gpt-5.3-codex"]},
        },
    )

    with patch("shutil.which", return_value="/usr/local/bin/codex"):
        session = instance.authenticate({}, store)

    assert session.authenticated is True
    assert session.instance == "codex_local"
    assert store.get("codex_local") is not None


def test_codex_cli_driver_instance_streaming_converts_json_events_into_chunks(tmp_path: Path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "token", "refresh_token": "refresh"},
            }
        ),
        encoding="utf-8",
    )
    store = InMemoryCredentialStore()
    driver = CodexCliDriver()
    instance = driver.create_instance(
        "codex_local",
        {
            "connection": {"auth_file": str(auth_file), "codex_binary": "codex"},
            "catalog": {"models": ["gpt-5.3-codex"]},
        },
    )

    with patch("shutil.which", return_value="/usr/local/bin/codex"):
        instance.authenticate({}, store)
    client = instance.get_client(store)

    class _FakePopen:
        def __init__(self, *_args, **_kwargs) -> None:
            self.stdout = iter(
                [
                    '{"type":"response.output_text.delta","delta":"你好"}\n',
                    '{"type":"response.output_text.delta","delta":"，世界"}\n',
                    '{"type":"turn.completed"}\n',
                ]
            )
            self.returncode = 0

        def wait(self, timeout=None):  # noqa: ANN001
            return 0

    with patch("subprocess.Popen", return_value=_FakePopen()):
        chunks = list(
            client.chat.completions.create(
                model="gpt-5.3-codex",
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
        )

    assert [chunk.choices[0].delta.content for chunk in chunks] == ["你好", "，世界"]


def test_model_registry_can_build_and_register_instance_from_driver():
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_driver(_FakeDriver())

    instance = registry.create_instance("fake", "worker", {"api_key": "secret"})
    registry.register_instance(instance)
    auth = registry.authenticate("worker", {"api_key": "secret"})

    assert auth.authenticated is True
    assert any(profile.model_name == "fast-model" for profile in registry.list_models("worker"))
