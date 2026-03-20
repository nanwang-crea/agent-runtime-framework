from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ModelProfile:
    provider: str
    model_name: str
    display_name: str
    supports_chat: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    context_window: int | None = None
    cost_level: str = "medium"
    latency_level: str = "medium"
    reasoning_level: str = "medium"
    recommended_roles: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AuthSession:
    provider: str
    authenticated: bool
    auth_type: str
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModelRuntime:
    profile: ModelProfile
    client: Any


class CredentialStore(Protocol):
    def get(self, provider: str) -> dict[str, Any] | None: ...

    def set(self, provider: str, credentials: dict[str, Any]) -> None: ...

    def delete(self, provider: str) -> None: ...


class InMemoryCredentialStore:
    def __init__(self) -> None:
        self._credentials: dict[str, dict[str, Any]] = {}

    def get(self, provider: str) -> dict[str, Any] | None:
        stored = self._credentials.get(provider)
        return dict(stored) if stored is not None else None

    def set(self, provider: str, credentials: dict[str, Any]) -> None:
        self._credentials[provider] = dict(credentials)

    def delete(self, provider: str) -> None:
        self._credentials.pop(provider, None)


class ModelProvider(Protocol):
    provider_name: str

    def list_models(self) -> list[ModelProfile]: ...

    def authenticate(self, credentials: dict[str, Any], store: CredentialStore) -> AuthSession: ...

    def get_client(self, store: CredentialStore) -> Any: ...


class ModelRegistry:
    def __init__(self, *, credential_store: CredentialStore | None = None) -> None:
        self.credential_store = credential_store or InMemoryCredentialStore()
        self._providers: dict[str, ModelProvider] = {}
        self._auth_sessions: dict[str, AuthSession] = {}

    def register_provider(self, provider: ModelProvider) -> None:
        self._providers[provider.provider_name] = provider

    def reset(self) -> None:
        self._providers.clear()
        self._auth_sessions.clear()
        self.credential_store = InMemoryCredentialStore()

    def provider(self, provider_name: str) -> ModelProvider:
        provider = self._providers.get(provider_name)
        if provider is None:
            raise KeyError(f"unknown provider: {provider_name}")
        return provider

    def provider_names(self) -> list[str]:
        return list(self._providers.keys())

    def list_models(self, provider_name: str | None = None) -> list[ModelProfile]:
        if provider_name is not None:
            return self.provider(provider_name).list_models()
        profiles: list[ModelProfile] = []
        for provider in self._providers.values():
            profiles.extend(provider.list_models())
        return profiles

    def authenticate(self, provider_name: str, credentials: dict[str, Any]) -> AuthSession:
        provider = self.provider(provider_name)
        session = provider.authenticate(credentials, self.credential_store)
        self._auth_sessions[provider_name] = session
        return session

    def auth_session(self, provider_name: str) -> AuthSession | None:
        return self._auth_sessions.get(provider_name)

    def get_client(self, provider_name: str) -> Any:
        return self.provider(provider_name).get_client(self.credential_store)


class ModelRouter:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry
        self._routes: dict[str, tuple[str, str]] = {}

    def set_route(self, role: str, *, provider: str, model_name: str) -> None:
        self._routes[role] = (provider, model_name)

    def reset(self) -> None:
        self._routes.clear()

    def get_route(self, role: str) -> dict[str, str] | None:
        route = self._routes.get(role)
        if route is None:
            return None
        provider, model_name = route
        return {"provider": provider, "model_name": model_name}

    def routes_payload(self) -> dict[str, dict[str, str]]:
        return {
            role: {"provider": provider, "model_name": model_name}
            for role, (provider, model_name) in self._routes.items()
        }

    def resolve(self, role: str) -> ModelRuntime | None:
        route = self._routes.get(role) or self._routes.get("default")
        if route is None:
            return None
        provider_name, model_name = route
        profile = next(
            (item for item in self.registry.list_models(provider_name) if item.model_name == model_name),
            None,
        )
        if profile is None:
            return None
        client = self.registry.get_client(provider_name)
        if client is None:
            return None
        return ModelRuntime(profile=profile, client=client)


def resolve_model_runtime(context: Any, role: str) -> ModelRuntime | None:
    model_router = context.services.get("model_router")
    if isinstance(model_router, ModelRouter):
        runtime = model_router.resolve(role)
        if runtime is not None:
            return runtime
    llm_client = getattr(context, "llm_client", None)
    llm_model = getattr(context, "llm_model", "")
    if llm_client is None or not llm_model:
        return None
    return ModelRuntime(
        profile=ModelProfile(
            provider="default",
            model_name=str(llm_model),
            display_name=str(llm_model),
            recommended_roles=[role],
        ),
        client=llm_client,
    )
