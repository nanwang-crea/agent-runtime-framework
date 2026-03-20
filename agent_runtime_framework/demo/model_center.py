from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from agent_runtime_framework.models import ModelRegistry, ModelRouter


MODEL_ROLES = (
    "default",
    "conversation",
    "capability_selector",
    "planner",
    "interpreter",
    "resolver",
    "executor",
    "composer",
)

DEFAULT_V3_CONFIG: dict[str, Any] = {
    "schema_version": 3,
    "provider_instances": {
        "openai": {
            "type": "openai_compatible",
            "enabled": True,
            "connection": {"base_url": "https://api.openai.com/v1"},
            "credentials": {"api_key": ""},
            "catalog": {"mode": "static", "models": ["gpt-4.1-mini", "gpt-4.1"]},
        },
        "dashscope": {
            "type": "openai_compatible",
            "enabled": True,
            "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
            "credentials": {"api_key": ""},
            "catalog": {"mode": "static", "models": ["qwen3.5-plus", "qwen-plus"]},
        },
        "minimax": {
            "type": "openai_compatible",
            "enabled": True,
            "connection": {"base_url": "https://api.minimax.chat/v1"},
            "credentials": {"api_key": ""},
            "catalog": {"mode": "static", "models": ["MiniMax-M2.1"]},
        },
        "codex_local": {
            "type": "codex_cli",
            "enabled": True,
            "connection": {"codex_binary": "codex", "auth_file": "~/.codex/auth.json"},
            "credentials": {},
            "catalog": {"mode": "static", "models": ["gpt-5.3-codex", "gpt-5.4", "gpt-5.4-mini"]},
        },
    },
    "routes": {
        "default": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "conversation": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "capability_selector": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "planner": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "interpreter": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "resolver": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "executor": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "composer": {"instance": "dashscope", "model": "qwen3.5-plus"},
    },
}


DriverFactory = Callable[[str, dict[str, Any]], Any]


class ModelCenterStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self, seed: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            normalized = migrate_config_to_v3(payload)
            self.save(normalized)
            return normalized
        created = migrate_config_to_v3(seed or DEFAULT_V3_CONFIG)
        self.save(created)
        return created

    def save(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.load_or_create()
        merged = migrate_config_to_v3(current)

        incoming_instances = patch.get("provider_instances") or patch.get("providers")
        if isinstance(incoming_instances, dict):
            for instance_id, instance_patch in incoming_instances.items():
                if not isinstance(instance_patch, dict):
                    continue
                current_instance = dict(merged["provider_instances"].get(instance_id, {}))
                current_instance = _deep_merge(current_instance, instance_patch)
                merged["provider_instances"][instance_id] = current_instance

        incoming_routes = patch.get("routes")
        if isinstance(incoming_routes, dict):
            for role, route_patch in incoming_routes.items():
                if not isinstance(route_patch, dict):
                    continue
                instance_id = str(route_patch.get("instance") or route_patch.get("provider") or "").strip()
                model = str(route_patch.get("model") or route_patch.get("model_name") or "").strip()
                if instance_id and model:
                    merged["routes"][role] = {"instance": instance_id, "model": model}

        self.save(merged)
        return merged


class ModelCenterService:
    def __init__(
        self,
        *,
        store: ModelCenterStore,
        registry: ModelRegistry,
        router: ModelRouter,
        driver_factories: dict[str, DriverFactory],
    ) -> None:
        self.store = store
        self.registry = registry
        self.router = router
        self.driver_factories = driver_factories

    def load(self) -> dict[str, Any]:
        config = self.store.load_or_create()
        return self._apply_and_project(config)

    def payload(self, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
        active_config = config or self.store.load_or_create()
        return self._apply_and_project(active_config)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        updated = self.store.update(patch)
        return self._apply_and_project(updated)

    def run_action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        normalized_action = action.strip()
        if normalized_action not in {"authenticate_instance", "authenticate_provider", "refresh_catalog"}:
            raise ValueError(f"unknown model center action: {action}")
        config = self.store.load_or_create()
        if normalized_action in {"authenticate_instance", "authenticate_provider"}:
            target = str(body.get("instance") or body.get("provider") or "").strip()
            if target:
                runtime = self._runtime_state(config)
                if target in runtime["instances"]:
                    runtime["instances"][target] = self._runtime_state_for_instance(
                        target,
                        dict(config["provider_instances"].get(target) or {}),
                    )
                    return {
                        "config": config,
                        "runtime": runtime,
                        "runtime_checks": {"config_path": str(self.store.path)},
                    }
        return self._apply_and_project(config)

    def _apply_and_project(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = migrate_config_to_v3(config)
        self.registry.reset()
        self.router.reset()

        for instance_id, instance_cfg in (normalized.get("provider_instances") or {}).items():
            if not bool(instance_cfg.get("enabled", True)):
                continue
            provider = self._provider_for_instance(instance_id, instance_cfg)
            if provider is None:
                continue
            self.registry.register_provider(provider)
            self._authenticate_instance(instance_id, instance_cfg)

        for role in MODEL_ROLES:
            route_cfg = dict((normalized.get("routes") or {}).get(role) or {})
            instance_id = str(route_cfg.get("instance") or "").strip()
            model = str(route_cfg.get("model") or "").strip()
            if instance_id and model:
                self.router.set_route(role, provider=instance_id, model_name=model)

        return {
            "config": normalized,
            "runtime": self._runtime_state(normalized),
            "runtime_checks": {"config_path": str(self.store.path)},
        }

    def _runtime_state(self, config: dict[str, Any]) -> dict[str, Any]:
        instances: dict[str, Any] = {}
        for instance_id, instance_cfg in (config.get("provider_instances") or {}).items():
            instances[instance_id] = self._runtime_state_for_instance(instance_id, dict(instance_cfg or {}))
        return {
            "instances": instances,
            "routes": {
                role: {"instance": route["provider"], "model": route["model_name"]}
                for role, route in self.router.routes_payload().items()
            },
        }

    def _runtime_state_for_instance(self, instance_id: str, instance_cfg: dict[str, Any]) -> dict[str, Any]:
        session = self.registry.auth_session(instance_id)
        models = []
        try:
            models = [item.as_dict() for item in self.registry.list_models(instance_id)]
        except KeyError:
            models = []
        return {
            "type": str(instance_cfg.get("type") or ""),
            "enabled": bool(instance_cfg.get("enabled", True)),
            "authenticated": bool(session and session.authenticated),
            "auth_error": str(session.error_message or "") if session else "",
            "models": models,
        }

    def _provider_for_instance(self, instance_id: str, instance_cfg: dict[str, Any]) -> Any:
        driver_type = str(instance_cfg.get("type") or "").strip()
        factory = self.driver_factories.get(driver_type)
        if factory is None:
            return None
        return factory(instance_id, instance_cfg)

    def _authenticate_instance(self, instance_id: str, instance_cfg: dict[str, Any]) -> None:
        connection = dict(instance_cfg.get("connection") or {})
        credentials = dict(instance_cfg.get("credentials") or {})
        payload = {**connection, **credentials}
        self.registry.authenticate(instance_id, payload)


def migrate_config_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    schema_version = int(payload.get("schema_version") or 0)
    if schema_version >= 3:
        normalized = _deep_merge(json.loads(json.dumps(DEFAULT_V3_CONFIG)), payload)
        normalized["schema_version"] = 3
        normalized["provider_instances"] = _normalize_provider_instances(normalized.get("provider_instances") or {})
        normalized["routes"] = _normalize_routes(normalized.get("routes") or {})
        normalized.pop("providers", None)
        normalized.pop("models", None)
        return normalized

    if schema_version == 2:
        return _migrate_v2_to_v3(payload)
    return _migrate_v1_to_v3(payload)


def _migrate_v1_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_V3_CONFIG))
    providers = payload.get("providers") or {}
    routes = payload.get("routes") or {}
    for instance_id, provider_payload in providers.items():
        provider_obj = _default_instance(instance_id)
        provider_obj["credentials"]["api_key"] = str((provider_payload or {}).get("api_key") or "")
        if "base_url" in (provider_payload or {}):
            provider_obj["connection"]["base_url"] = str((provider_payload or {}).get("base_url") or "")
        merged["provider_instances"][instance_id] = provider_obj
    merged["routes"] = _normalize_routes(routes)
    return merged


def _migrate_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_V3_CONFIG))
    providers = payload.get("providers") or {}
    models = payload.get("models") or {}
    routes = payload.get("routes") or {}
    for instance_id, provider_payload in providers.items():
        provider_obj = _default_instance(instance_id)
        provider_payload = dict(provider_payload or {})
        provider_obj["type"] = str(provider_payload.get("type") or provider_obj["type"])
        provider_obj["enabled"] = bool(provider_payload.get("enabled", True))
        provider_obj["connection"] = _deep_merge(provider_obj["connection"], dict(provider_payload.get("connection") or {}))
        provider_obj["credentials"] = _deep_merge(provider_obj["credentials"], dict(provider_payload.get("credentials") or {}))
        catalog_models = list(models.get(instance_id) or provider_obj["catalog"]["models"])
        provider_obj["catalog"] = {"mode": "static", "models": catalog_models}
        merged["provider_instances"][instance_id] = provider_obj
    merged["routes"] = _normalize_routes(routes)
    return merged


def _normalize_provider_instances(instances: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized = {name: _default_instance(name) for name in DEFAULT_V3_CONFIG["provider_instances"]}
    for instance_id, instance_cfg in instances.items():
        normalized[instance_id] = _deep_merge(_default_instance(instance_id), dict(instance_cfg or {}))
        normalized[instance_id].pop("auth", None)
    return normalized


def _normalize_routes(routes: dict[str, Any]) -> dict[str, dict[str, str]]:
    normalized = {role: dict(value) for role, value in (DEFAULT_V3_CONFIG["routes"] or {}).items()}
    for role, route in routes.items():
        if not isinstance(route, dict):
            continue
        instance_id = str(route.get("instance") or route.get("provider") or "").strip()
        model = str(route.get("model") or route.get("model_name") or "").strip()
        if instance_id and model:
            normalized[role] = {"instance": instance_id, "model": model}
    for role in MODEL_ROLES:
        if role not in normalized:
            normalized[role] = dict(DEFAULT_V3_CONFIG["routes"][role])
    return normalized


def _default_instance(instance_id: str) -> dict[str, Any]:
    defaults = dict((DEFAULT_V3_CONFIG["provider_instances"] or {}).get(instance_id) or {})
    if defaults:
        return json.loads(json.dumps(defaults))
    if instance_id == "codex_local":
        return {
            "type": "codex_cli",
            "enabled": True,
            "connection": {"codex_binary": "codex", "auth_file": "~/.codex/auth.json"},
            "credentials": {},
            "catalog": {"mode": "static", "models": []},
        }
    return {
        "type": "openai_compatible",
        "enabled": True,
        "connection": {"base_url": ""},
        "credentials": {"api_key": ""},
        "catalog": {"mode": "static", "models": []},
    }


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged
