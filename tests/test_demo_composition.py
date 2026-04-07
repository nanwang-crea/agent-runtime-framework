from pathlib import Path


def test_demo_package_has_been_removed():
    demo_dir = Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "demo"

    assert not demo_dir.exists()


def test_api_runtime_state_no_longer_owns_error_normalization_helpers():
    source = (Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api" / "state" / "runtime_state.py").read_text(encoding="utf-8")

    assert "def _normalize_error(" not in source
    assert "def _with_router_trace(" not in source


def test_api_runtime_state_no_longer_owns_payload_and_history_helpers():
    source = (Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api" / "state" / "runtime_state.py").read_text(encoding="utf-8")

    assert "def _result_payload(" not in source
    assert "def _record_run(" not in source


def test_api_runtime_state_is_no_longer_a_view_payload_surface():
    source = (Path(__file__).resolve().parents[1] / "agent_runtime_framework" / "api" / "state" / "runtime_state.py").read_text(encoding="utf-8")

    assert "def context_payload(" not in source
    assert "def session_payload(" not in source
    assert "def memory_payload(" not in source
    assert "def plan_history_payload(" not in source
    assert "def run_history_payload(" not in source
    assert "def switch_context(" not in source
    assert "def error_payload(" not in source
    assert "def result_payload(" not in source
