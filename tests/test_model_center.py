from __future__ import annotations

from agent_runtime_framework.api.services.model_center_service import normalize_config_v3


def test_normalize_config_v3_fills_missing_sections():
    payload = {
        "schema_version": 3,
        "instances": {
            "dashscope": {
                "type": "openai_compatible",
                "enabled": True,
                "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
                "credentials": {"api_key": ""},
                "catalog": {"mode": "static", "models": ["qwen3.5-plus"]},
            }
        },
        "routes": {"conversation": {"instance": "dashscope", "model": "qwen3.5-plus"}},
    }

    normalized = normalize_config_v3(payload)

    assert normalized["schema_version"] == 3
    assert "openai" in normalized["instances"]
    assert "default" in normalized["routes"]
    assert "router" in normalized["routes"]
    assert "evaluator" in normalized["routes"]
    assert "planner" in normalized["routes"]

def test_normalize_config_v3_ignores_unknown_top_level_sections():
    payload = {
        "schema_version": 3,
        "instances": {
            "dashscope": {
                "type": "openai_compatible",
                "enabled": True,
                "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
                "credentials": {"api_key": ""},
                "catalog": {"mode": "static", "models": ["qwen3.5-plus"]},
            }
        },
        "providers": {"legacy": {"api_key": "old"}},
        "models": {"legacy": ["m1"]},
        "routes": {"conversation": {"instance": "dashscope", "model": "qwen3.5-plus"}},
    }

    normalized = normalize_config_v3(payload)

    assert normalized["instances"]["dashscope"]["catalog"]["models"] == ["qwen3.5-plus"]


def test_normalize_config_v3_preserves_openai_wire_api():
    payload = {
        "schema_version": 3,
        "instances": {
            "openai": {
                "type": "openai_compatible",
                "enabled": True,
                "connection": {
                    "base_url": "https://ice.v.ua/v1",
                    "wire_api": "responses",
                },
                "credentials": {"api_key": "sk-test"},
                "catalog": {"mode": "static", "models": ["gpt-5.4"]},
            }
        },
        "routes": {"planner": {"instance": "openai", "model": "gpt-5.4"}},
    }

    normalized = normalize_config_v3(payload)

    assert normalized["instances"]["openai"]["connection"]["wire_api"] == "responses"
