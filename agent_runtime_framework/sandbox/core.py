from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

from agent_runtime_framework.core.errors import AppError


_DEFAULT_ALLOWED_COMMANDS = (
    "cat",
    "echo",
    "git",
    "ls",
    "make",
    "node",
    "npm",
    "pwd",
    "python",
    "python3",
    "pytest",
)

_READ_ONLY_COMMANDS = (
    "cat",
    "echo",
    "git",
    "ls",
    "pwd",
    "pytest",
)

_NETWORK_BLOCKED_COMMANDS = (
    "curl",
    "ftp",
    "nc",
    "ncat",
    "scp",
    "ssh",
    "telnet",
    "wget",
)

_SHELL_META_PATTERN = re.compile(r"[|&;><`]")


@dataclass(slots=True)
class SandboxConfig:
    mode: str = "workspace_write"
    workspace_root: Path | None = None
    writable_roots: list[Path] = field(default_factory=list)
    allow_network: bool = False
    allowed_commands: tuple[str, ...] = _DEFAULT_ALLOWED_COMMANDS
    read_only_commands: tuple[str, ...] = _READ_ONLY_COMMANDS
    blocked_commands: tuple[str, ...] = _NETWORK_BLOCKED_COMMANDS
    max_execution_seconds: int = 30

    def normalized_workspace_root(self) -> Path:
        if self.workspace_root is None:
            raise AppError(
                code="SANDBOX_MISCONFIGURED",
                message="Sandbox 缺少工作区根目录配置。",
                detail="workspace_root is not configured",
                stage="sandbox",
                retriable=False,
                suggestion="请先为当前运行时配置 sandbox workspace_root。",
            )
        return self.workspace_root.expanduser().resolve()

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "workspace_root": str(self.normalized_workspace_root()),
            "writable_roots": [str(path.expanduser().resolve()) for path in self.writable_roots],
            "allow_network": self.allow_network,
            "allowed_commands": list(self.allowed_commands),
        }


def resolve_sandbox(context: Any) -> SandboxConfig:
    services = context.application_context.services
    sandbox = services.get("sandbox")
    if isinstance(sandbox, SandboxConfig):
        return sandbox
    roots = getattr(context.application_context.resource_repository, "allowed_roots", [])
    workspace_root = Path(roots[0]).expanduser().resolve() if roots else None
    sandbox = SandboxConfig(
        mode=str(context.application_context.config.get("sandbox_mode") or "workspace_write"),
        workspace_root=workspace_root,
        writable_roots=[workspace_root] if workspace_root is not None else [],
        allow_network=bool(context.application_context.config.get("sandbox_allow_network", False)),
    )
    services["sandbox"] = sandbox
    return sandbox


def run_sandboxed_command(command: str, context: Any, *, timeout: int | None = None) -> dict[str, Any]:
    sandbox = resolve_sandbox(context)
    workspace_root = sandbox.normalized_workspace_root()
    argv = _normalize_command(command)
    executable = argv[0]
    _assert_command_allowed(argv, sandbox)
    completed = subprocess.run(
        argv,
        shell=False,
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
        timeout=min(timeout or sandbox.max_execution_seconds, sandbox.max_execution_seconds),
    )
    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    return {
        "command": command,
        "argv": list(argv),
        "returncode": completed.returncode,
        "stdout": output,
        "stderr": error,
        "text": output if output else error,
        "success": completed.returncode == 0,
        "sandbox": sandbox.to_payload(),
        "sandbox_applied": True,
    }


def _normalize_command(command: str) -> list[str]:
    stripped = command.strip()
    if not stripped:
        raise AppError(
            code="SANDBOX_INVALID_COMMAND",
            message="Shell 命令不能为空。",
            detail="missing command",
            stage="sandbox",
            retriable=True,
            suggestion="请提供一个明确的命令，例如 `pwd` 或 `pytest -q`。",
        )
    if _SHELL_META_PATTERN.search(stripped) or "$(" in stripped:
        raise AppError(
            code="SANDBOX_DENIED",
            message="Sandbox 拒绝包含 shell 元字符的命令。",
            detail=f"command requires shell parsing: {stripped}",
            stage="sandbox",
            retriable=False,
            suggestion="请改为不依赖 shell 语法的直接命令，例如 `python3 -m pytest -q`。",
        )
    try:
        argv = shlex.split(stripped)
    except ValueError as exc:
        raise AppError(
            code="SANDBOX_INVALID_COMMAND",
            message="命令格式无法被安全解析。",
            detail=str(exc),
            stage="sandbox",
            retriable=True,
            suggestion="请检查引号或转义是否完整。",
        ) from exc
    if not argv:
        raise AppError(
            code="SANDBOX_INVALID_COMMAND",
            message="Shell 命令不能为空。",
            detail="empty argv after parsing",
            stage="sandbox",
            retriable=True,
            suggestion="请提供一个明确的命令，例如 `pwd`。",
        )
    return argv


def _assert_command_allowed(argv: list[str], sandbox: SandboxConfig) -> None:
    executable = argv[0]
    if executable in sandbox.blocked_commands and not sandbox.allow_network:
        raise AppError(
            code="SANDBOX_DENIED",
            message="Sandbox 阻止了潜在网络命令。",
            detail=f"network command blocked: {executable}",
            stage="sandbox",
            retriable=False,
            suggestion="如确有必要，请显式开启 network policy 或改用本地工具。",
        )
    if executable not in sandbox.allowed_commands:
        raise AppError(
            code="SANDBOX_DENIED",
            message="当前 sandbox 模式不允许执行该命令。",
            detail=f"command not allowed: {executable}",
            stage="sandbox",
            retriable=False,
            suggestion="请改用允许的开发命令，或在更高权限模式下重试。",
        )
    if sandbox.mode == "read_only" and executable not in sandbox.read_only_commands:
        raise AppError(
            code="SANDBOX_DENIED",
            message="read_only 模式下不允许执行该命令。",
            detail=f"command requires elevated mode: {executable}",
            stage="sandbox",
            retriable=False,
            suggestion="请切换到 `workspace_write` 模式后再执行该命令。",
        )
