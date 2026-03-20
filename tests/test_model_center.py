from __future__ import annotations

import json
from pathlib import Path

from agent_runtime_framework.demo.model_center import migrate_config_to_v3


def test_migrate_config_to_v3_from_v1_shape():
    v1 = {
        "providers": {
            "dashscope": {
                "api_key": "sk-test",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            }
        },
        "routes": {
            "conversation": {"provider": "dashscope", "model_name": "qwen3.5-plus"},
            "capability_selector": {"provider": "dashscope", "model_name": "qwen3.5-plus"},
            "planner": {"provider": "dashscope", "model_name": "qwen3.5-plus"},
        },
    }

    migrated = migrate_config_to_v3(v1)

    assert migrated["schema_version"] == 3
    assert migrated["provider_instances"]["dashscope"]["type"] == "openai_compatible"
    assert migrated["provider_instances"]["dashscope"]["credentials"]["api_key"] == "sk-test"
    assert migrated["provider_instances"]["dashscope"]["catalog"]["models"] == ["qwen3.5-plus", "qwen-plus"]
    assert migrated["routes"]["conversation"] == {"instance": "dashscope", "model": "qwen3.5-plus"}


def test_migrate_config_to_v3_from_v2_shape_drops_persisted_auth(tmp_path: Path):
    payload = {
        "schema_version": 2,
        "providers": {
            "dashscope": {
                "type": "openai_compatible",
                "enabled": True,
                "connection": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
                "credentials": {"api_key": ""},
                "auth": {"mode": "api_key", "status": "failed", "last_error": "bad key"},
            }
        },
        "models": {"dashscope": ["qwen3.5-plus"]},
        "routes": {"conversation": {"provider": "dashscope", "model": "qwen3.5-plus"}},
    }
    file_path = tmp_path / ".arf_demo_config.json"
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    migrated = migrate_config_to_v3(json.loads(file_path.read_text(encoding="utf-8")))

    assert migrated["schema_version"] == 3
    assert "provider_instances" in migrated
    assert "providers" not in migrated
    assert "models" not in migrated
    assert "auth" not in migrated["provider_instances"]["dashscope"]
    assert migrated["provider_instances"]["dashscope"]["catalog"]["models"] == ["qwen3.5-plus"]
    assert migrated["routes"]["conversation"] == {"instance": "dashscope", "model": "qwen3.5-plus"}

