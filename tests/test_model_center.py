from __future__ import annotations

from agent_runtime_framework.demo.model_center import normalize_config_v3


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


def test_normalize_config_v3_drops_legacy_sections():
    payload = {
        "schema_version": 3,
        "providers": {"legacy": {"api_key": "old"}},
        "models": {"legacy": ["m1"]},
        "provider_instances": {
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

    assert "providers" not in normalized
    assert "models" not in normalized
    assert normalized["instances"]["dashscope"]["catalog"]["models"] == ["qwen3.5-plus"]
