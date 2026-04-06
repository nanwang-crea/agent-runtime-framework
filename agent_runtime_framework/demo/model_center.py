from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_runtime_framework.core.errors import AppError, log_app_error, normalize_app_error
from agent_runtime_framework.models import ModelRegistry, ModelRouter

logger = logging.getLogger(__name__)


MODEL_ROLES = (
    "default",
    "router",
    "evaluator",
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
    "instances": {
        "openai": {
            "type": "openai_compatible",
            "enabled": True,
            "connection": {"base_url": "https://api.openai.com/v1", "wire_api": "chat_completions"},
            "credentials": {"api_key": ""},
            "catalog": {"mode": "static", "models": ["gpt-5.4"]},
        },
        "dashscope": {
            "type": "openai_compatible",
            "enabled": True,
            "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "wire_api": "chat_completions"},
            "credentials": {"api_key": ""},
            "catalog": {"mode": "static", "models": ["qwen3.5-plus", "qwen-plus"]},
        },
        "minimax": {
            "type": "openai_compatible",
            "enabled": True,
            "connection": {"base_url": "https://api.minimax.chat/v1", "wire_api": "chat_completions"},
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
        "router": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "evaluator": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "conversation": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "capability_selector": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "planner": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "interpreter": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "resolver": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "executor": {"instance": "dashscope", "model": "qwen3.5-plus"},
        "composer": {"instance": "dashscope", "model": "qwen3.5-plus"},
    },
}

class ModelCenterStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self, seed: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            normalized = normalize_config_v3(payload)
            self.save(normalized)
            return normalized
        created = normalize_config_v3(seed or DEFAULT_V3_CONFIG)
        self.save(created)
        return created

    def save(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.load_or_create()
        merged = normalize_config_v3(current)

        incoming_instances = patch.get("instances") or patch.get("provider_instances")
        if isinstance(incoming_instances, dict):
            for instance_id, instance_patch in incoming_instances.items():
                if not isinstance(instance_patch, dict):
                    continue
                current_instance = dict(merged["instances"].get(instance_id, {}))
                current_instance = _deep_merge(current_instance, _normalize_instance_patch(current_instance, instance_patch))
                merged["instances"][instance_id] = current_instance

        incoming_routes = patch.get("routes")
        if isinstance(incoming_routes, dict):
            for role, route_patch in incoming_routes.items():
                if not isinstance(route_patch, dict):
                    continue
                instance_id = str(route_patch.get("instance") or "").strip()
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
    ) -> None:
        self.store = store
        self.registry = registry
        self.router = router

    def load(self) -> dict[str, Any]:
        try:
            config = self.store.load_or_create()
            return self._apply_and_project(config)
        except Exception as exc:
            error = normalize_app_error(exc, code="MODEL_CENTER_LOAD_FAILED", message="加载模型中心配置失败。", stage="model_center", context={"operation": "load"})
            log_app_error(logger, error, exc=exc, event="model_center_load_failed")
            raise error

    def payload(self, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            active_config = config or self.store.load_or_create()
            return self._apply_and_project(active_config)
        except Exception as exc:
            error = normalize_app_error(exc, code="MODEL_CENTER_PAYLOAD_FAILED", message="读取模型中心运行时状态失败。", stage="model_center", context={"operation": "payload"})
            log_app_error(logger, error, exc=exc, event="model_center_payload_failed")
            raise error

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        try:
            updated = self.store.update(patch)
            return self._apply_and_project(updated)
        except Exception as exc:
            error = normalize_app_error(exc, code="MODEL_CENTER_UPDATE_FAILED", message="更新模型中心配置失败。", stage="model_center", context={"operation": "update"})
            log_app_error(logger, error, exc=exc, event="model_center_update_failed")
            raise error

    def run_action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            body = payload or {}
            normalized_action = action.strip()
            if normalized_action not in {"authenticate_instance", "refresh_catalog"}:
                raise AppError(
                    code="MODEL_CENTER_ACTION_UNKNOWN",
                    message="未知的模型中心动作。",
                    detail=f"unknown model center action: {action}",
                    stage="model_center",
                    retriable=False,
                    suggestion="请检查前端传入的 action 是否受支持。",
                    context={
                        "action": normalized_action or action,
                        "allowed_actions": ["authenticate_instance", "refresh_catalog"],
                    },
                )
            config = self.store.load_or_create()
            if normalized_action == "authenticate_instance":
                target = str(body.get("instance") or "").strip()
                if target:
                    runtime = self._runtime_state(config)
                    if target in runtime["instances"]:
                        runtime["instances"][target] = self._runtime_state_for_instance(
                            target,
                            dict(config["instances"].get(target) or {}),
                        )
                        return {
                            "config": config,
                            "runtime": runtime,
                            "runtime_checks": {"config_path": str(self.store.path)},
                        }
            return self._apply_and_project(config)
        except Exception as exc:
            error = normalize_app_error(
                exc,
                code="MODEL_CENTER_ACTION_FAILED",
                message="执行模型中心动作失败。",
                stage="model_center",
                context={"operation": "run_action", "action": action.strip() or action},
            )
            log_app_error(logger, error, exc=exc, event="model_center_action_failed")
            raise error

    def _apply_and_project(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_config_v3(config)
        self.registry.reset()
        self.router.reset()

        for instance_id, instance_cfg in (normalized.get("instances") or {}).items():
            if not bool(instance_cfg.get("enabled", True)):
                continue
            driver_type = str(instance_cfg.get("type") or "").strip()
            try:
                instance = self.registry.create_instance(driver_type, instance_id, instance_cfg)
            except KeyError:
                continue
            self.registry.register_instance(instance)
            self._authenticate_instance(instance_id, instance_cfg)

        for role in MODEL_ROLES:
            route_cfg = dict((normalized.get("routes") or {}).get(role) or {})
            instance_id = str(route_cfg.get("instance") or "").strip()
            model = str(route_cfg.get("model") or "").strip()
            if instance_id and model:
                self.router.set_route(role, instance_id=instance_id, model_name=model)

        return {
            "config": self._public_config(normalized),
            "runtime": self._runtime_state(normalized),
            "runtime_checks": {"config_path": str(self.store.path)},
        }

    def _public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        public = json.loads(json.dumps(config))
        for instance_cfg in (public.get("instances") or {}).values():
            if not isinstance(instance_cfg, dict):
                continue
            credentials = dict(instance_cfg.get("credentials") or {})
            api_key = str(credentials.get("api_key") or "")
            instance_cfg["api_key_set"] = bool(api_key)
            instance_cfg["api_key_preview"] = _mask_api_key(api_key)
            instance_cfg["credentials"] = {}
        return public

    def _runtime_state(self, config: dict[str, Any]) -> dict[str, Any]:
        instances: dict[str, Any] = {}
        for instance_id, instance_cfg in (config.get("instances") or {}).items():
            instances[instance_id] = self._runtime_state_for_instance(instance_id, dict(instance_cfg or {}))
        return {
            "instances": instances,
            "routes": {
                role: {"instance": route["instance"], "model": route["model_name"]}
                for role, route in self.router.routes_payload().items()
            },
            "default_instance": str((config.get("routes") or {}).get("default", {}).get("instance") or ""),
            "active_model": {
                "instance": str((config.get("routes") or {}).get("default", {}).get("instance") or ""),
                "model": str((config.get("routes") or {}).get("default", {}).get("model") or ""),
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
            "catalog_mode": str((instance_cfg.get("catalog") or {}).get("mode") or "static"),
            "authenticated": bool(session and session.authenticated),
            "auth_error": str(session.error_message or "") if session else "",
            "capabilities": self._driver_capabilities(str(instance_cfg.get("type") or "").strip()),
            "models": models,
        }

    def _driver_capabilities(self, driver_type: str) -> dict[str, Any]:
        capabilities = self.registry.driver_capabilities(driver_type)
        if capabilities is None:
            return {
                "supports_stream": False,
                "supports_tools": False,
                "supports_vision": False,
                "supports_json_mode": False,
            }
        if hasattr(capabilities, "as_dict"):
            return capabilities.as_dict()
        return {
            "supports_stream": bool(getattr(capabilities, "supports_stream", False)),
            "supports_tools": bool(getattr(capabilities, "supports_tools", False)),
            "supports_vision": bool(getattr(capabilities, "supports_vision", False)),
            "supports_json_mode": bool(getattr(capabilities, "supports_json_mode", False)),
        }

    def _authenticate_instance(self, instance_id: str, instance_cfg: dict[str, Any]) -> None:
        connection = dict(instance_cfg.get("connection") or {})
        credentials = dict(instance_cfg.get("credentials") or {})
        payload = {**connection, **credentials}
        self.registry.authenticate(instance_id, payload)


def normalize_config_v3(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _deep_merge(json.loads(json.dumps(DEFAULT_V3_CONFIG)), payload)
    normalized["schema_version"] = 3
    legacy_instances = normalized.pop("provider_instances", None)
    merged_instances = dict(normalized.get("instances") or {})
    if isinstance(legacy_instances, dict):
        merged_instances = _deep_merge(merged_instances, legacy_instances)
    normalized["instances"] = _normalize_instances(merged_instances)
    normalized["routes"] = _normalize_routes(normalized.get("routes") or {})
    normalized.pop("providers", None)
    normalized.pop("models", None)
    return normalized


def _normalize_instances(instances: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized = {name: _default_instance(name) for name in DEFAULT_V3_CONFIG["instances"]}
    for instance_id, instance_cfg in instances.items():
        normalized[instance_id] = _deep_merge(_default_instance(instance_id), dict(instance_cfg or {}))
        normalized[instance_id].pop("auth", None)
    return normalized


def _normalize_routes(routes: dict[str, Any]) -> dict[str, dict[str, str]]:
    normalized = {role: dict(value) for role, value in (DEFAULT_V3_CONFIG["routes"] or {}).items()}
    for role, route in routes.items():
        if not isinstance(route, dict):
            continue
        instance_id = str(route.get("instance") or "").strip()
        model = str(route.get("model") or route.get("model_name") or "").strip()
        if instance_id and model:
            normalized[role] = {"instance": instance_id, "model": model}
    for role in MODEL_ROLES:
        if role not in normalized:
            normalized[role] = dict(DEFAULT_V3_CONFIG["routes"][role])
    return normalized


def _default_instance(instance_id: str) -> dict[str, Any]:
    defaults = dict((DEFAULT_V3_CONFIG["instances"] or {}).get(instance_id) or {})
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
        "connection": {"base_url": "", "wire_api": "chat_completions"},
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


def _mask_api_key(value: str) -> str:
    token = value.strip()
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}***{token[-4:]}"


def _normalize_instance_patch(current_instance: dict[str, Any], instance_patch: dict[str, Any]) -> dict[str, Any]:
    normalized_patch = json.loads(json.dumps(instance_patch))
    credentials = normalized_patch.get("credentials")
    if not isinstance(credentials, dict):
        return normalized_patch
    if str(credentials.get("api_key") or "").strip():
        return normalized_patch
    current_api_key = str(((current_instance.get("credentials") or {}).get("api_key") or "")).strip()
    if not current_api_key:
        return normalized_patch
    credentials = dict(credentials)
    credentials.pop("api_key", None)
    if credentials:
        normalized_patch["credentials"] = credentials
    else:
        normalized_patch.pop("credentials", None)
    return normalized_patch
