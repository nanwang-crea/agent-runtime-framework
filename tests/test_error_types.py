from agent_runtime_framework.errors import (
    AgentRuntimeError,
    AppError,
    PolicyViolationError,
    ToolExecutionError,
)
from agent_runtime_framework.sandbox.core import SandboxConfig, _assert_command_allowed, _normalize_command, _assert_workspace_operands_allowed


def test_framework_errors_are_typed():
    assert issubclass(PolicyViolationError, AgentRuntimeError)
    assert issubclass(ToolExecutionError, AgentRuntimeError)


def test_app_error_serializes_trace_id_and_context():
    error = AppError(
        code="INTERNAL_ERROR",
        message="boom",
        stage="run",
        context={"workspace": "/tmp/demo", "route": "codex"},
    )

    payload = error.as_dict()

    assert payload["trace_id"]
    assert payload["context"] == {"workspace": "/tmp/demo", "route": "codex"}


def test_sandbox_denial_reasons_are_stable_for_shell_meta():
    try:
        _normalize_command("ls | rg foo")
    except AppError as exc:
        assert exc.context["failure_category"] == "sandbox_policy"
        assert exc.context["failure_subcategory"] == "shell_meta_denied"
        assert exc.context["suggested_recovery_mode"] == "repair_arguments"
    else:
        raise AssertionError("expected AppError")


def test_sandbox_denial_reasons_are_stable_for_workspace_operands(tmp_path):
    sandbox = SandboxConfig(workspace_root=tmp_path, writable_roots=[tmp_path])

    try:
        _assert_workspace_operands_allowed(["touch", "../outside.txt"], sandbox)
    except AppError as exc:
        assert exc.context["failure_category"] == "sandbox_policy"
        assert exc.context["failure_subcategory"] == "path_outside_workspace"
    else:
        raise AssertionError("expected AppError")


def test_sandbox_denial_reasons_are_stable_for_read_only_violation(tmp_path):
    sandbox = SandboxConfig(mode="read_only", workspace_root=tmp_path, writable_roots=[tmp_path])

    try:
        _assert_command_allowed(["mkdir", "docs"], sandbox)
    except AppError as exc:
        assert exc.context["failure_category"] == "sandbox_policy"
        assert exc.context["failure_subcategory"] == "read_only_violation"
        assert exc.context["suggested_recovery_mode"] == "repair_environment"
    else:
        raise AssertionError("expected AppError")
