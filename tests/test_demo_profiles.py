from importlib import import_module
from pathlib import Path
import pytest


def test_api_models_package_no_longer_exports_agent_profiles():
    with pytest.raises(ModuleNotFoundError):
        import_module("agent_runtime_framework.api.models.agent_profiles")


def test_agent_profile_module_file_is_removed():
    api_root = Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api"
    assert not (api_root / "models" / "agent_profiles.py").exists()
