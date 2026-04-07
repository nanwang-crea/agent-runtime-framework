from pathlib import Path


def test_demo_package_exposes_bootstrap_module_for_app_creation():
    source = (Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "demo" / "__init__.py").read_text(encoding="utf-8")

    assert "create_demo_assistant_app" in source


def test_demo_app_no_longer_owns_error_normalization_helpers():
    source = (Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "demo" / "app.py").read_text(encoding="utf-8")

    assert "def _normalize_error(" not in source
    assert "def _with_router_trace(" not in source


def test_demo_app_no_longer_owns_payload_and_history_helpers():
    source = (Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "demo" / "app.py").read_text(encoding="utf-8")

    assert "def _result_payload(" not in source
    assert "def _record_run(" not in source


def test_demo_app_no_longer_owns_view_state_payload_helpers():
    source = (Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "demo" / "app.py").read_text(encoding="utf-8")

    assert "def context_payload(" in source
    assert "def session_payload(" in source
    assert "def memory_payload(" in source
    assert "def plan_history_payload(" in source
    assert "def run_history_payload(" in source
    assert "return build_context_payload(" in source
    assert "return build_session_payload(" in source
    assert "return build_memory_payload(" in source
    assert "return build_plan_history_payload(" in source
    assert "return build_run_history_payload(" in source
