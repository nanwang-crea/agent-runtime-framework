from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "providers": {
        "dashscope": {
            "api_key": "",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
    },
    "routes": {
        "conversation": {"provider": "dashscope", "model_name": "qwen3.5-plus"},
        "capability_selector": {"provider": "dashscope", "model_name": "qwen3.5-plus"},
        "planner": {"provider": "dashscope", "model_name": "qwen3.5-plus"},
    },
}


class DemoConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_CONFIG))
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_or_create(self, seed: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.path.exists():
            return self.load()
        payload = json.loads(json.dumps(seed or DEFAULT_CONFIG))
        self.save(payload)
        return payload

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.load_or_create()
        merged = json.loads(json.dumps(current))
        if "providers" in payload and isinstance(payload["providers"], dict):
            for provider_name, provider_payload in payload["providers"].items():
                existing = dict(merged.setdefault("providers", {}).get(provider_name, {}))
                existing.update(provider_payload or {})
                merged["providers"][provider_name] = existing
        if "routes" in payload and isinstance(payload["routes"], dict):
            for role, route_payload in payload["routes"].items():
                merged.setdefault("routes", {})[role] = dict(route_payload or {})
        self.save(merged)
        return merged


def config_payload(config: dict[str, Any], *, path: Path) -> dict[str, Any]:
    providers = []
    for provider_name, provider_payload in (config.get("providers") or {}).items():
        api_key = str((provider_payload or {}).get("api_key") or "")
        providers.append(
            {
                "provider": provider_name,
                "api_key_set": bool(api_key),
                "api_key_preview": _mask_api_key(api_key),
                "base_url": str((provider_payload or {}).get("base_url") or ""),
            }
        )
    return {
        "path": str(path),
        "providers": providers,
        "routes": dict(config.get("routes") or {}),
    }


def _mask_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:5]}...{value[-4:]}"
