from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from agent_runtime_framework.core.specs import ToolSpec
from agent_runtime_framework.resources import ResourceRef
from agent_runtime_framework.sandbox import run_sandboxed_command


def _output_limit(context: Any, key: str, default: int) -> int:
    value = context.application_context.config.get(key, default)
    try:
        return max(80, int(value))
    except (TypeError, ValueError):
        return default


def _truncate_text(text: str, *, limit: int, label: str = "输出") -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit].rstrip()}\n\n[{label}已截断，保留前 {limit} 个字符。]"


def _build_agent_output(
    *,
    path: str,
    text: str,
    summary: str,
    truncated: bool = False,
    next_hint: str = "",
    changed_paths: list[str] | None = None,
    items: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "text": text,
        "content": text,
        "summary": summary,
        "truncated": truncated,
        "next_hint": next_hint,
        "changed_paths": list(changed_paths or []),
        "items": list(items or []),
        "entities": {"path": path, "items": list(items or [])},
    }


def build_default_codex_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="list_workspace_directory",
            description="List a directory inside the current workspace.",
            executor=_list_workspace_directory,
            input_schema={"path": "string"},
            permission_level="metadata_read",
            prompt_snippet="List files and directories from the current workspace.",
            prompt_guidelines=["Use list_workspace_directory before broad file reads when you need structure."],
        ),
        ToolSpec(
            name="read_workspace_text",
            description="Read a text file inside the current allowed workspace.",
            executor=_read_workspace_text,
            input_schema={"path": "string"},
            permission_level="content_read",
            prompt_snippet="Read concise text from workspace files.",
            prompt_guidelines=["Prefer read_workspace_text over shell cat for normal file inspection."],
        ),
        ToolSpec(
            name="summarize_workspace_text",
            description="Summarize a text file inside the current workspace.",
            executor=_summarize_workspace_text,
            input_schema={"path": "string"},
            permission_level="content_read",
            prompt_snippet="Summarize a workspace file when raw text is unnecessary.",
            prompt_guidelines=["Prefer summarize_workspace_text when the user asks for a summary instead of full content."],
        ),
        ToolSpec(
            name="inspect_workspace_path",
            description="Inspect a workspace directory or file and produce a concise structural explanation.",
            executor=_inspect_workspace_path,
            input_schema={"path": "string"},
            permission_level="content_read",
            prompt_snippet="Inspect a path and explain its structure.",
            prompt_guidelines=["Use inspect_workspace_path after a directory listing when the user asks for architecture or module roles."],
        ),
        ToolSpec(
            name="run_shell_command",
            description="Run a shell command in the current workspace and capture stdout/stderr.",
            executor=_run_shell_command,
            input_schema={"command": "string"},
            permission_level="safe_write",
            timeout_seconds=30.0,
            prompt_snippet="Run an allowed workspace shell command inside the sandbox.",
            prompt_guidelines=["Use run_shell_command only when a dedicated workspace tool cannot answer the request."],
        ),
        ToolSpec(
            name="apply_text_patch",
            description="Replace a text fragment inside a workspace file and return the updated content.",
            executor=_apply_text_patch,
            input_schema={"path": "string", "search_text": "string", "replace_text": "string"},
            permission_level="safe_write",
            prompt_snippet="Apply a surgical text patch to a workspace file.",
            prompt_guidelines=["Use apply_text_patch for targeted edits when the exact old text is known."],
            serialize_by_argument="path",
        ),
        ToolSpec(
            name="move_workspace_path",
            description="Move or rename a file inside the current workspace.",
            executor=_move_workspace_path,
            input_schema={"path": "string", "destination_path": "string"},
            permission_level="safe_write",
            prompt_snippet="Move or rename a workspace file.",
            prompt_guidelines=["Use move_workspace_path instead of shell mv for workspace file moves."],
            serialize_by_argument="path",
        ),
        ToolSpec(
            name="delete_workspace_path",
            description="Delete a file inside the current workspace.",
            executor=_delete_workspace_path,
            input_schema={"path": "string"},
            permission_level="destructive_write",
            prompt_snippet="Delete a workspace file after explicit confirmation.",
            prompt_guidelines=["Only delete workspace files when the user clearly requested removal."],
            serialize_by_argument="path",
        ),
        ToolSpec(
            name="create_workspace_path",
            description="Create a file or directory inside the current workspace.",
            executor=_create_workspace_path,
            input_schema={"path": "string", "content": "string", "kind": "string"},
            permission_level="safe_write",
            prompt_snippet="Create a new file or directory inside the workspace.",
            prompt_guidelines=["Use create_workspace_path for new files or folders instead of shell mkdir/touch."],
            serialize_by_argument="path",
        ),
        ToolSpec(
            name="edit_workspace_text",
            description="Replace the full text content of a workspace file.",
            executor=_edit_workspace_text,
            input_schema={"path": "string", "content": "string"},
            permission_level="safe_write",
            prompt_snippet="Replace the full contents of a workspace file.",
            prompt_guidelines=["Use edit_workspace_text only for full rewrites, not surgical patches."],
            serialize_by_argument="path",
        ),
    ]


def _workspace_root(context: Any) -> Path:
    roots = getattr(context.application_context.resource_repository, "allowed_roots", [])
    if not roots:
        raise RuntimeError("no allowed workspace roots configured")
    return Path(roots[0]).expanduser().resolve()


def _resolve_workspace_path(context: Any, path_arg: str) -> Path:
    root = _workspace_root(context)
    candidate = Path(path_arg).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not (resolved == root or root in resolved.parents):
        raise ValueError(f"path is outside allowed roots: {resolved}")
    return resolved


def _resolve_resource_ref(
    context: Any,
    path_arg: str,
    *,
    use_last_focus: bool = False,
    use_default_directory: bool = False,
) -> ResourceRef:
    repository = context.application_context.resource_repository
    if use_last_focus:
        snapshot = context.application_context.session_memory.snapshot()
        if snapshot.focused_resources:
            return snapshot.focused_resources[0]
    if path_arg:
        try:
            return ResourceRef.for_path(_resolve_workspace_path(context, path_arg))
        except FileNotFoundError:
            root_ref = ResourceRef.for_path(_workspace_root(context))
            matches = repository.find_by_name(root_ref, path_arg)
            if matches:
                return matches[0]
            raise
    if use_default_directory:
        return ResourceRef.for_path(_workspace_root(context))
    raise ValueError("missing path")


def _remember_focus(context: Any, ref: ResourceRef, summary: str) -> None:
    context.application_context.session_memory.remember_focus([ref], summary=summary)


def _list_workspace_directory(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
        use_default_directory=bool(arguments.get("use_default_directory")),
    )
    items = context.application_context.resource_repository.list_directory(ref)
    directories = [item.title for item in items if item.kind == "directory"]
    files = [item.title for item in items if item.kind == "file"]
    lines = [f"下面一共有 {len(items)} 个条目。"]
    if directories:
        lines.append(f"目录：{', '.join(directories)}")
    if files:
        lines.append(f"文件：{', '.join(files)}")
    text = "\n".join(lines)
    _remember_focus(context, ref, text)
    return _build_agent_output(
        path=ref.location,
        text=text,
        summary=lines[0],
        next_hint="如果需要理解目录职责，下一步调用 inspect_workspace_path。",
        items=[item.title for item in items],
    )


def _read_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
    )
    content = context.application_context.resource_repository.load_text(ref)
    limited = _truncate_text(content, limit=_output_limit(context, "codex_max_read_chars", 4000))
    summary = "\n".join(limited.splitlines()[:3]) if limited.strip() else ""
    _remember_focus(context, ref, summary)
    return _build_agent_output(
        path=ref.location,
        text=limited,
        summary=summary or f"Read {Path(ref.location).name}",
        truncated=limited != content.strip(),
        next_hint="如果需要更多内容，继续读取同一路径或改用 summarize/inspect。",
    )


def _summarize_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
    )
    content = context.application_context.resource_repository.load_text(ref)
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    summary = "\n".join(lines[:3]) if lines else content[:300]
    summary = _truncate_text(summary, limit=_output_limit(context, "codex_max_summary_chars", 1000), label="摘要")
    _remember_focus(context, ref, summary)
    return _build_agent_output(
        path=ref.location,
        text=summary,
        summary=summary,
        truncated=summary != content[: len(summary)],
        next_hint="如果摘要不够，下一步读取原文或 inspect 相关路径。",
    )


def _inspect_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
        use_default_directory=not bool(arguments.get("path")),
    )
    path = Path(ref.location)
    if path.is_file():
        text = _summarize_file(path)
        _remember_focus(context, ref, text)
        return _build_agent_output(
            path=str(path),
            text=text,
            summary=text,
            next_hint="如果需要原文细节，下一步读取该文件。",
        )
    items = context.application_context.resource_repository.list_directory(ref)
    directories = [item for item in items if item.kind == "directory"]
    files = [item for item in items if item.kind == "file"]
    lines = [f"{path.name or str(path)} 下面共有 {len(items)} 个条目。"]
    if directories:
        lines.append("子目录：")
        for item in directories[:8]:
            lines.append(f"- {item.title}/：模块目录。")
    if files:
        lines.append("关键文件：")
        for item in files[:8]:
            file_path = Path(item.location)
            lines.append(f"- {item.title}：{_summarize_file(file_path)}")
    text = "\n".join(lines)
    text = _truncate_text(text, limit=_output_limit(context, "codex_max_inspect_chars", 2000))
    _remember_focus(context, ref, text)
    return _build_agent_output(
        path=str(path),
        text=text,
        summary=lines[0],
        next_hint="如果需要细化模块作用，继续读取关键文件。",
        items=[item.title for item in items],
    )


def _run_shell_command(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    command = str(arguments.get("command") or "").strip()
    if not command:
        raise ValueError("missing command")
    result = run_sandboxed_command(command, context, timeout=30)
    text = str(result.get("text") or "")
    truncated_text = _truncate_text(text, limit=_output_limit(context, "codex_max_shell_chars", 2000))
    result["text"] = truncated_text
    if result.get("stdout"):
        result["stdout"] = _truncate_text(str(result["stdout"]), limit=_output_limit(context, "codex_max_shell_chars", 2000))
    elif result.get("stderr"):
        result["stderr"] = _truncate_text(str(result["stderr"]), limit=_output_limit(context, "codex_max_shell_chars", 2000))
    result["summary"] = truncated_text.splitlines()[0] if truncated_text else command
    result["truncated"] = truncated_text != text.strip()
    result["next_hint"] = "如果这是验证命令，检查 success 字段；否则结合上下文决定是否继续读取文件。"
    result["changed_paths"] = []
    result["entities"] = {"path": "", "items": []}
    return result


def _summarize_file(path: Path) -> str:
    if not path.exists():
        return "文件不存在。"
    if path.suffix == ".py":
        return _summarize_python_file(path)
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip().strip("#").strip()
        if stripped:
            return stripped[:120]
    return "文件内容较少。"


def _summarize_python_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    try:
        module = ast.parse(text)
    except SyntaxError:
        return "Python 模块。"
    docstring = ast.get_docstring(module)
    if docstring:
        return docstring.strip().splitlines()[0][:120]
    symbols = [node.name for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    if symbols:
        return f"定义了 {', '.join(symbols[:4])}"
    return "Python 模块。"


def _apply_text_patch(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_workspace_path(context, str(arguments.get("path") or ""))
    if not path.exists():
        raise FileNotFoundError(path)
    search_text = str(arguments.get("search_text") or "")
    replace_text = str(arguments.get("replace_text") or "")
    if not search_text:
        raise ValueError("missing search_text")
    original = path.read_text(encoding="utf-8")
    if search_text not in original:
        raise ValueError("search_text not found in target file")
    updated = original.replace(search_text, replace_text, 1)
    path.write_text(updated, encoding="utf-8")
    ref = ResourceRef.for_path(path)
    summary = "\n".join(updated.splitlines()[:3]) if updated.strip() else ""
    _remember_focus(context, ref, summary)
    truncated = _truncate_text(updated, limit=_output_limit(context, "codex_max_write_chars", 2000))
    return {
        **_build_agent_output(
            path=str(path),
            text=truncated,
            summary=f"Patched {path.name}",
            truncated=truncated != updated.strip(),
            next_hint="下一步应运行验证或重新读取文件确认修改。",
            changed_paths=[path.name],
        ),
        "before_text": original,
        "after_text": updated,
    }


def _move_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    source = _resolve_workspace_path(context, str(arguments.get("path") or ""))
    destination = _resolve_workspace_path(context, str(arguments.get("destination_path") or ""))
    if not source.exists():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    ref = ResourceRef.for_path(destination)
    summary = f"moved: {source.name} -> {destination.name}"
    _remember_focus(context, ref, summary)
    return {
        **_build_agent_output(
            path=str(destination),
            text=summary,
            summary=summary,
            next_hint="如果需要确认内容未变，下一步读取目标文件。",
            changed_paths=[destination.name],
        ),
        "destination_path": str(destination),
    }


def _delete_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_workspace_path(context, str(arguments.get("path") or ""))
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_dir():
        raise IsADirectoryError(path)
    path.unlink()
    context.application_context.session_memory.remember_focus([], summary=f"deleted: {path.name}")
    return _build_agent_output(
        path=str(path),
        text=f"deleted: {path.name}",
        summary=f"Deleted {path.name}",
        next_hint="如果这是计划中的清理操作，下一步运行验证或继续其余修改。",
        changed_paths=[path.name],
    )


def _create_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_workspace_path(context, str(arguments.get("path") or ""))
    kind = str(arguments.get("kind") or "file").strip().lower()
    content = str(arguments.get("content") or "")
    if path.exists():
        raise FileExistsError(path)
    if kind == "directory":
        path.mkdir(parents=True, exist_ok=False)
        text = f"created directory: {path.name}"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        text = content
    ref = ResourceRef.for_path(path)
    _remember_focus(context, ref, text[:200] if text else f"created: {path.name}")
    return {
        **_build_agent_output(
            path=str(path),
            text=text,
            summary=f"Created {path.name}",
            next_hint="如果这是代码文件，下一步读取或验证该文件内容。",
            changed_paths=[path.name],
        ),
        "content": content,
    }


def _edit_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_workspace_path(context, str(arguments.get("path") or ""))
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_dir():
        raise IsADirectoryError(path)
    content = str(arguments.get("content") or "")
    path.write_text(content, encoding="utf-8")
    ref = ResourceRef.for_path(path)
    _remember_focus(context, ref, content[:200] if content else f"edited: {path.name}")
    truncated = _truncate_text(content, limit=_output_limit(context, "codex_max_write_chars", 2000))
    return {
        **_build_agent_output(
            path=str(path),
            text=truncated,
            summary=f"Updated {path.name}",
            truncated=truncated != content.strip(),
            next_hint="下一步应运行验证或重新读取文件确认最终内容。",
            changed_paths=[path.name],
        ),
        "content": content,
    }
