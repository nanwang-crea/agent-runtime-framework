from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ModelProfile:
    instance: str
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
    instance: str
    authenticated: bool
    auth_type: str
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DriverCapabilities:
    supports_stream: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModelRuntime:
    profile: ModelProfile
    client: Any


class CredentialStore(Protocol):
    def get(self, instance_id: str) -> dict[str, Any] | None: ...

    def set(self, instance_id: str, credentials: dict[str, Any]) -> None: ...

    def delete(self, instance_id: str) -> None: ...


class InMemoryCredentialStore:
    def __init__(self) -> None:
        self._credentials: dict[str, dict[str, Any]] = {}

    def get(self, instance_id: str) -> dict[str, Any] | None:
        stored = self._credentials.get(instance_id)
        return dict(stored) if stored is not None else None

    def set(self, instance_id: str, credentials: dict[str, Any]) -> None:
        self._credentials[instance_id] = dict(credentials)

    def delete(self, instance_id: str) -> None:
        self._credentials.pop(instance_id, None)


class ModelInstance(Protocol):
    instance_id: str

    def list_models(self) -> list[ModelProfile]: ...

    def authenticate(self, credentials: dict[str, Any], store: CredentialStore) -> AuthSession: ...

    def get_client(self, store: CredentialStore) -> Any: ...


class ModelDriver(Protocol):
    driver_type: str
    capabilities: DriverCapabilities

    def create_instance(self, instance_id: str, config: dict[str, Any]) -> ModelInstance: ...


class ModelRegistry:
    def __init__(self, *, credential_store: CredentialStore | None = None) -> None:
        self.credential_store = credential_store or InMemoryCredentialStore()
        self._instances: dict[str, ModelInstance] = {}
        self._drivers: dict[str, ModelDriver] = {}
        self._auth_sessions: dict[str, AuthSession] = {}

    def register_driver(self, driver: ModelDriver) -> None:
        self._drivers[driver.driver_type] = driver

    def register_instance(self, instance: ModelInstance) -> None:
        self._instances[str(instance.instance_id)] = instance

    def create_instance(self, driver_type: str, instance_id: str, config: dict[str, Any]) -> ModelInstance:
        driver = self._drivers.get(driver_type)
        if driver is None:
            raise KeyError(f"unknown model driver: {driver_type}")
        return driver.create_instance(instance_id, config)

    def reset(self) -> None:
        self._instances.clear()
        self._auth_sessions.clear()
        self.credential_store = InMemoryCredentialStore()

    def instance(self, instance_id: str) -> ModelInstance:
        instance = self._instances.get(instance_id)
        if instance is None:
            raise KeyError(f"unknown model instance: {instance_id}")
        return instance

    def instance_names(self) -> list[str]:
        return list(self._instances.keys())

    def list_models(self, instance_id: str | None = None) -> list[ModelProfile]:
        if instance_id is not None:
            return self.instance(instance_id).list_models()
        profiles: list[ModelProfile] = []
        for instance in self._instances.values():
            profiles.extend(instance.list_models())
        return profiles

    def authenticate(self, instance_id: str, credentials: dict[str, Any]) -> AuthSession:
        instance = self.instance(instance_id)
        session = instance.authenticate(credentials, self.credential_store)
        self._auth_sessions[instance_id] = session
        return session

    def auth_session(self, instance_id: str) -> AuthSession | None:
        return self._auth_sessions.get(instance_id)

    def get_client(self, instance_id: str) -> Any:
        return self.instance(instance_id).get_client(self.credential_store)

    def driver_capabilities(self, driver_type: str) -> DriverCapabilities | None:
        driver = self._drivers.get(driver_type)
        return driver.capabilities if driver is not None else None


class ModelRouter:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry
        self._routes: dict[str, tuple[str, str]] = {}
        self._resolve_retry_limit = 2

    def set_route(self, role: str, *, instance_id: str, model_name: str) -> None:
        self._routes[role] = (instance_id, model_name)

    def reset(self) -> None:
        self._routes.clear()

    def get_route(self, role: str) -> dict[str, str] | None:
        route = self._routes.get(role)
        if route is None:
            return None
        instance_id, model_name = route
        return {"instance": instance_id, "model_name": model_name}

    def routes_payload(self) -> dict[str, dict[str, str]]:
        return {
            role: {"instance": instance_id, "model_name": model_name}
            for role, (instance_id, model_name) in self._routes.items()
        }

    def resolve(self, role: str) -> ModelRuntime | None:
        for instance_id, model_name in self._candidate_routes(role):
            profile = next((item for item in self.registry.list_models(instance_id) if item.model_name == model_name), None)
            if profile is None:
                continue
            client = self._resolve_client_with_retry(instance_id)
            if client is None:
                continue
            return ModelRuntime(profile=profile, client=client)
        return None

    def _candidate_routes(self, role: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for route in (self._routes.get(role), self._routes.get("default")):
            if route is None or route in seen:
                continue
            candidates.append(route)
            seen.add(route)
        for route in self._routes.values():
            if route in seen:
                continue
            candidates.append(route)
            seen.add(route)
        return candidates

    def _resolve_client_with_retry(self, instance_id: str) -> Any | None:
        for attempt in range(self._resolve_retry_limit):
            try:
                client = self.registry.get_client(instance_id)
            except Exception:
                if attempt + 1 >= self._resolve_retry_limit:
                    return None
                continue
            if client is not None:
                return client
        return None


def resolve_model_runtime(context: Any, role: str) -> ModelRuntime | None:
    services = getattr(context, "services", {}) or {}
    model_router = services.get("model_router")
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
            instance="default",
            model_name=str(llm_model),
            display_name=str(llm_model),
            recommended_roles=[role],
        ),
        client=llm_client,
    )
