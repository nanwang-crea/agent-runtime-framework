from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Any

from agent_runtime_framework.workflow.workspace.tools.base import WorkspaceToolDefinition
from agent_runtime_framework.workflow.workspace.tools.common import build_agent_output, candidate_paths, relative_workspace_path, resolve_workspace_path, truncate_text, workspace_root
from agent_runtime_framework.sandbox import run_sandboxed_command


def build_shell_tools() -> list[WorkspaceToolDefinition]:
    return [
        WorkspaceToolDefinition(
            name="run_shell_command",
            description="Run an allowed shell command inside the workspace sandbox.",
            handler=run_shell_command,
            input_schema={"command": "string"},
            permission_level="safe_write",
            required_arguments=("command",),
            prompt_snippet="Run an allowed shell command in the sandbox.",
            prompt_guidelines=["Use shell mainly for verification, tests, git, and build commands."],
            timeout_seconds=120.0,
        ),
        WorkspaceToolDefinition(
            name="grep_workspace",
            description="Search for text across workspace files.",
            handler=grep_workspace,
            input_schema={"pattern": "string", "path": "string", "context_lines": "integer", "file_glob": "string"},
            permission_level="content_read",
            required_arguments=("pattern",),
            prompt_snippet="Search workspace files for a pattern.",
            prompt_guidelines=["Use this to locate definitions, usages, or keywords before opening files."],
        ),
        WorkspaceToolDefinition(
            name="search_workspace_symbols",
            description="Find function, class, or variable definitions across Python files.",
            handler=search_workspace_symbols,
            input_schema={"symbol": "string", "path": "string", "kind": "string"},
            permission_level="content_read",
            required_arguments=("symbol",),
            prompt_snippet="Search for Python symbols in the workspace.",
            prompt_guidelines=["Use this before editing code when you need definition locations."],
        ),
        WorkspaceToolDefinition(
            name="get_git_diff",
            description="Get the current git diff.",
            handler=get_git_diff,
            input_schema={"path": "string", "staged": "boolean"},
            permission_level="metadata_read",
            prompt_snippet="Show the current git diff for the workspace.",
            prompt_guidelines=["Use after edits to review the patch."],
        ),
        WorkspaceToolDefinition(
            name="run_tests",
            description="Run tests from the workspace and return a summary.",
            handler=run_tests,
            input_schema={"path": "string", "pattern": "string", "timeout": "integer"},
            permission_level="safe_write",
            prompt_snippet="Run tests in the workspace sandbox.",
            prompt_guidelines=["Prefer this over raw shell commands for test execution."],
            timeout_seconds=120.0,
        ),
    ]


def run_shell_command(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    command = str(arguments.get("command") or "").strip()
    result = run_sandboxed_command(command, context=context)
    text = str(result.get("text") or "")
    return {**result, **build_agent_output(context=context, path="", text=text, summary=f"command: {command}")}


def grep_workspace(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    pattern = str(arguments.get("pattern") or "")
    base_path = str(arguments.get("path") or ".")
    context_lines = int(arguments.get("context_lines") or 0)
    file_glob = str(arguments.get("file_glob") or "")
    root = resolve_workspace_path(context, base_path)
    results: list[str] = []
    regex = re.compile(pattern)
    for path in candidate_paths(root if root.is_dir() else root.parent, max_depth=6):
        if not path.is_file():
            continue
        if file_glob and not path.match(file_glob):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for index, line in enumerate(lines):
            if not regex.search(line):
                continue
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            snippet = "\n".join(f"{i + 1}:{lines[i]}" for i in range(start, end))
            results.append(f"## {relative_workspace_path(context, path)}\n{snippet}")
            if len(results) >= 20:
                break
        if len(results) >= 20:
            break
    text = "\n\n".join(results) if results else "No matches found."
    return build_agent_output(context=context, path=str(root), text=truncate_text(text, limit=4000, label="grep"), summary=f"found {len(results)} matches")


def search_workspace_symbols(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    symbol = str(arguments.get("symbol") or "")
    kind = str(arguments.get("kind") or "all").lower()
    base_path = resolve_workspace_path(context, str(arguments.get("path") or "."))
    search_root = base_path if base_path.is_dir() else base_path.parent
    results: list[str] = []
    for path in candidate_paths(search_root, max_depth=6):
        if not path.is_file() or path.suffix != ".py":
            continue
        try:
            module = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in module.body:
            node_kind = ""
            node_name = ""
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node_kind, node_name = "function", node.name
            elif isinstance(node, ast.ClassDef):
                node_kind, node_name = "class", node.name
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        node_kind, node_name = "variable", target.id
                        break
            if not node_name or symbol not in node_name:
                continue
            if kind != "all" and kind != node_kind:
                continue
            results.append(f"- {relative_workspace_path(context, path)}: {node_kind} {node_name}")
    text = "\n".join(results) if results else "No matching symbols found."
    return build_agent_output(context=context, path=relative_workspace_path(context, search_root), text=text, summary=f"found {len(results)} symbols")


def get_git_diff(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = str(arguments.get("path") or "").strip()
    staged = bool(arguments.get("staged") or False)
    parts = ["git", "diff"]
    if staged:
        parts.append("--staged")
    if path:
        parts.extend(["--", path])
    result = run_sandboxed_command(" ".join(parts), context=context)
    text = str(result.get("text") or "")
    return {**result, **build_agent_output(context=context, path=path, text=text, summary="git diff")}


def run_tests(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = str(arguments.get("path") or "").strip()
    pattern = str(arguments.get("pattern") or "").strip()
    if pattern:
        command = f"pytest {pattern} -q"
    elif path:
        command = f"pytest {path} -q"
    else:
        command = "pytest -q"
    result = run_sandboxed_command(command, context=context)
    text = str(result.get("text") or "")
    return {
        **result,
        **build_agent_output(context=context, path=path, text=text, summary="tests executed"),
        "success": bool(result.get("success")),
    }
