from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent_runtime_framework.core.specs import ToolSpec
from agent_runtime_framework.resources import ResourceRef


def build_default_codex_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="list_workspace_directory",
            description="List a directory inside the current workspace.",
            executor=_list_workspace_directory,
            input_schema={"path": "string"},
            permission_level="metadata_read",
        ),
        ToolSpec(
            name="read_workspace_text",
            description="Read a text file inside the current allowed workspace.",
            executor=_read_workspace_text,
            input_schema={"path": "string"},
            permission_level="content_read",
        ),
        ToolSpec(
            name="summarize_workspace_text",
            description="Summarize a text file inside the current workspace.",
            executor=_summarize_workspace_text,
            input_schema={"path": "string"},
            permission_level="content_read",
        ),
        ToolSpec(
            name="run_shell_command",
            description="Run a shell command in the current workspace and capture stdout/stderr.",
            executor=_run_shell_command,
            input_schema={"command": "string"},
            permission_level="safe_write",
            timeout_seconds=30.0,
        ),
        ToolSpec(
            name="apply_text_patch",
            description="Replace a text fragment inside a workspace file and return the updated content.",
            executor=_apply_text_patch,
            input_schema={"path": "string", "search_text": "string", "replace_text": "string"},
            permission_level="safe_write",
        ),
        ToolSpec(
            name="move_workspace_path",
            description="Move or rename a file inside the current workspace.",
            executor=_move_workspace_path,
            input_schema={"path": "string", "destination_path": "string"},
            permission_level="safe_write",
        ),
        ToolSpec(
            name="delete_workspace_path",
            description="Delete a file inside the current workspace.",
            executor=_delete_workspace_path,
            input_schema={"path": "string"},
            permission_level="destructive_write",
        ),
        ToolSpec(
            name="create_workspace_path",
            description="Create a file or directory inside the current workspace.",
            executor=_create_workspace_path,
            input_schema={"path": "string", "content": "string", "kind": "string"},
            permission_level="safe_write",
        ),
        ToolSpec(
            name="edit_workspace_text",
            description="Replace the full text content of a workspace file.",
            executor=_edit_workspace_text,
            input_schema={"path": "string", "content": "string"},
            permission_level="safe_write",
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
    return {"path": ref.location, "items": [item.title for item in items], "text": text}


def _read_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
    )
    content = context.application_context.resource_repository.load_text(ref)
    summary = "\n".join(content.splitlines()[:3]) if content.strip() else ""
    _remember_focus(context, ref, summary)
    return {
        "path": ref.location,
        "content": content,
        "text": content,
    }


def _summarize_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
    )
    content = context.application_context.resource_repository.load_text(ref)
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    summary = "\n".join(lines[:3]) if lines else content[:300]
    _remember_focus(context, ref, summary)
    return {
        "path": ref.location,
        "content": summary,
        "text": summary,
    }


def _run_shell_command(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    command = str(arguments.get("command") or "").strip()
    if not command:
        raise ValueError("missing command")
    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(_workspace_root(context)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": output,
        "stderr": error,
        "text": output if output else error,
        "success": completed.returncode == 0,
    }


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
    return {
        "path": str(path),
        "before_text": original,
        "after_text": updated,
        "text": updated,
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
        "path": str(source),
        "destination_path": str(destination),
        "text": summary,
    }


def _delete_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_workspace_path(context, str(arguments.get("path") or ""))
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_dir():
        raise IsADirectoryError(path)
    path.unlink()
    context.application_context.session_memory.remember_focus([], summary=f"deleted: {path.name}")
    return {
        "path": str(path),
        "text": f"deleted: {path.name}",
    }


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
        "path": str(path),
        "text": text,
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
    return {
        "path": str(path),
        "text": content,
        "content": content,
    }
