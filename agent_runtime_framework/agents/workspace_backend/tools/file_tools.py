from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from agent_runtime_framework.agents.workspace_backend.tools.base import WorkspaceToolDefinition
from agent_runtime_framework.agents.workspace_backend.tools.common import build_agent_output, candidate_paths, output_limit, record_evidence, relative_workspace_path, remember_focus, resolve_workspace_path, score_match, summarize_path, truncate_text, workspace_root


def build_file_tools() -> list[WorkspaceToolDefinition]:
    return [
        WorkspaceToolDefinition(
            name="resolve_workspace_target",
            description="Resolve a file or directory in the current workspace from a query or path hint.",
            handler=resolve_workspace_target,
            input_schema={"query": "string", "target_hint": "string"},
            permission_level="content_read",
            prompt_snippet="Resolve a workspace target before reading or explaining it.",
            prompt_guidelines=["Use resolve_workspace_target when the user mentions a file/module/folder but the exact path is uncertain."],
        ),
        WorkspaceToolDefinition(
            name="read_workspace_text",
            description="Read a workspace file and return its content.",
            handler=read_workspace_text,
            input_schema={"path": "string"},
            permission_level="content_read",
            required_arguments=("path",),
            serialize_by_argument="path",
            prompt_snippet="Read a workspace file as UTF-8 text.",
            prompt_guidelines=["Use this after resolving the target file path."],
        ),
        WorkspaceToolDefinition(
            name="read_workspace_excerpt",
            description="Read a workspace file and return a truncated excerpt.",
            handler=read_workspace_excerpt,
            input_schema={"path": "string"},
            permission_level="content_read",
            required_arguments=("path",),
            serialize_by_argument="path",
            prompt_snippet="Read a capped excerpt from a workspace file.",
            prompt_guidelines=["Prefer this when the file may be long."],
        ),
        WorkspaceToolDefinition(
            name="summarize_workspace_text",
            description="Summarize a workspace file without returning the full content.",
            handler=summarize_workspace_text,
            input_schema={"path": "string"},
            permission_level="content_read",
            required_arguments=("path",),
            serialize_by_argument="path",
            prompt_snippet="Summarize a workspace file.",
            prompt_guidelines=["Use for quick file understanding before deeper reading."],
        ),
        WorkspaceToolDefinition(
            name="inspect_workspace_path",
            description="Inspect a workspace directory or file and return a structural summary.",
            handler=inspect_workspace_path,
            input_schema={"path": "string"},
            permission_level="content_read",
            required_arguments=("path",),
            prompt_snippet="Inspect a workspace path before explaining it.",
            prompt_guidelines=["Use this for package/folder overviews."],
        ),
        WorkspaceToolDefinition(
            name="list_workspace_directory",
            description="List entries inside a workspace directory.",
            handler=list_workspace_directory,
            input_schema={"path": "string"},
            permission_level="content_read",
            required_arguments=("path",),
            prompt_snippet="List a workspace directory.",
            prompt_guidelines=["Use this for directory browsing."],
        ),
        WorkspaceToolDefinition(
            name="rank_workspace_entries",
            description="Rank representative entries within a workspace directory.",
            handler=rank_workspace_entries,
            input_schema={"path": "string", "query": "string", "limit": "integer"},
            permission_level="content_read",
            required_arguments=("path",),
            prompt_snippet="Pick representative files for an explanation.",
            prompt_guidelines=["Use after inspecting a directory when you need the most relevant files."],
        ),
        WorkspaceToolDefinition(
            name="extract_workspace_outline",
            description="Extract a lightweight outline from a workspace file.",
            handler=extract_workspace_outline,
            input_schema={"path": "string"},
            permission_level="content_read",
            required_arguments=("path",),
            prompt_snippet="Read only the high-level outline of a workspace file.",
            prompt_guidelines=["Useful for representative-file explanations."],
        ),
        WorkspaceToolDefinition(
            name="apply_text_patch",
            description="Replace a text fragment inside a workspace file.",
            handler=apply_text_patch,
            input_schema={"path": "string", "search_text": "string", "replace_text": "string"},
            permission_level="safe_write",
            required_arguments=("path", "search_text"),
            serialize_by_argument="path",
            prompt_snippet="Apply a surgical text patch to a workspace file.",
            prompt_guidelines=["Use for targeted edits when the exact original text is known."],
        ),
        WorkspaceToolDefinition(
            name="append_workspace_text",
            description="Append text to a workspace file.",
            handler=append_workspace_text,
            input_schema={"path": "string", "content": "string"},
            permission_level="safe_write",
            required_arguments=("path", "content"),
            serialize_by_argument="path",
            prompt_snippet="Append text to a workspace file.",
            prompt_guidelines=["Prefer dedicated file tools over shell redirection."],
        ),
        WorkspaceToolDefinition(
            name="move_workspace_path",
            description="Move or rename a file inside the workspace.",
            handler=move_workspace_path,
            input_schema={"path": "string", "destination_path": "string"},
            permission_level="safe_write",
            required_arguments=("path", "destination_path"),
            prompt_snippet="Move or rename a workspace file.",
            prompt_guidelines=["Use instead of shell mv for workspace moves."],
        ),
        WorkspaceToolDefinition(
            name="delete_workspace_path",
            description="Delete a file inside the workspace.",
            handler=delete_workspace_path,
            input_schema={"path": "string"},
            permission_level="destructive_write",
            required_arguments=("path",),
            prompt_snippet="Delete a workspace file.",
            prompt_guidelines=["Only delete when the user explicitly requested removal."],
        ),
        WorkspaceToolDefinition(
            name="create_workspace_path",
            description="Create a file or directory inside the workspace.",
            handler=create_workspace_path,
            input_schema={"path": "string", "content": "string", "kind": "string"},
            permission_level="safe_write",
            required_arguments=("path",),
            prompt_snippet="Create a new workspace file or directory.",
            prompt_guidelines=["Use this instead of shell mkdir/touch."],
        ),
        WorkspaceToolDefinition(
            name="edit_workspace_text",
            description="Replace the full contents of a workspace file.",
            handler=edit_workspace_text,
            input_schema={"path": "string", "content": "string"},
            permission_level="safe_write",
            required_arguments=("path", "content"),
            serialize_by_argument="path",
            prompt_snippet="Replace a workspace file with new content.",
            prompt_guidelines=["Use for full rewrites, not surgical edits."],
        ),
    ]


def resolve_workspace_target(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    root = workspace_root(context)
    query = str(arguments.get("query") or "").strip()
    target_hint = str(arguments.get("target_hint") or "").strip()
    candidate = target_hint or query
    if candidate:
        try:
            path = resolve_workspace_path(context, candidate)
            if path.exists():
                summary = summarize_path(path)
                remember_focus(context, path, summary)
                return {
                    **build_agent_output(context=context, path=relative_workspace_path(context, path), text=summary, summary=summary),
                    "resolution_status": "resolved",
                    "resolved_path": relative_workspace_path(context, path),
                    "resolved_kind": "directory" if path.is_dir() else "file",
                }
        except Exception:
            pass
    scored: list[tuple[int, int, str, Path]] = []
    match_query = target_hint or query
    preferred_path = str(arguments.get("preferred_path") or "").strip()
    scope_preference = str(arguments.get("scope_preference") or "").strip()
    exclude_paths = {str(item).strip() for item in arguments.get("exclude_paths", []) or [] if str(item).strip()}
    for path in candidate_paths(root):
        score_value, depth_score, relative = score_match(path, match_query, root)
        if exclude_paths and relative_workspace_path(context, path) in exclude_paths:
            continue
        if scope_preference == "workspace_root" and path.parent != root:
            continue
        if preferred_path and relative_workspace_path(context, path) == preferred_path:
            score_value += 100
        if score_value > 0:
            scored.append((score_value, depth_score, relative, path))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    matches = [candidate for _, _, _, candidate in scored[:5]]
    if not matches:
        text = f"未找到与 `{match_query or query}` 对应的工作区目标。"
        return {**build_agent_output(context=context, path="", text=text, summary=text), "resolution_status": "unresolved", "resolved_kind": "unknown"}
    if len(matches) > 1:
        best_score = scored[0][0]
        next_score = scored[1][0]
        if best_score <= next_score + 10:
            items = [relative_workspace_path(context, path) for path in matches]
            text = "多个可能目标：\n" + "\n".join(f"- {item}" for item in items)
            return {**build_agent_output(context=context, path="", text=text, summary="multiple candidate targets", items=items), "resolution_status": "ambiguous", "resolved_kind": "unknown"}
    path = matches[0]
    summary = summarize_path(path)
    remember_focus(context, path, summary)
    return {
        **build_agent_output(context=context, path=relative_workspace_path(context, path), text=summary, summary=summary),
        "resolution_status": "resolved",
        "resolved_path": relative_workspace_path(context, path),
        "resolved_kind": "directory" if path.is_dir() else "file",
    }


def read_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    text = path.read_text(encoding="utf-8")
    rendered = truncate_text(text, limit=output_limit(context, "workspace_max_read_chars", 4000), label="file")
    summary = summarize_path(path)
    remember_focus(context, path, summary)
    record_evidence(task, context, source="workspace", path=path, content=text, summary=summary)
    return build_agent_output(context=context, path=relative_workspace_path(context, path), text=rendered, summary=summary)


def read_workspace_excerpt(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    text = path.read_text(encoding="utf-8")
    excerpt = truncate_text(text, limit=min(1200, output_limit(context, "workspace_max_read_chars", 4000)), label="excerpt")
    summary = summarize_path(path)
    remember_focus(context, path, summary)
    record_evidence(task, context, source="workspace", path=path, content=excerpt, summary=summary)
    return build_agent_output(context=context, path=relative_workspace_path(context, path), text=excerpt, summary=summary)


def summarize_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    summary = summarize_path(path)
    remember_focus(context, path, summary)
    record_evidence(task, context, source="workspace", path=path, content=summary, summary=summary)
    return build_agent_output(context=context, path=relative_workspace_path(context, path), text=summary, summary=summary)


def inspect_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    if path.is_dir():
        children = sorted(path.iterdir(), key=lambda item: item.name)
        items = [child.name + ("/" if child.is_dir() else "") for child in children[:20]]
        text = "\n".join(items)
        summary = f"{relative_workspace_path(context, path)} contains {len(children)} entries"
        remember_focus(context, path, summary)
        record_evidence(task, context, source="workspace", path=path, content=text, summary=summary, kind="directory")
        return {**build_agent_output(context=context, path=relative_workspace_path(context, path), text=text, summary=summary, items=items), "resolved_kind": "directory"}
    return read_workspace_text(task, context, {"path": relative_workspace_path(context, path)})


def list_workspace_directory(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    return inspect_workspace_path(task, context, arguments)


def rank_workspace_entries(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    if not path.is_dir():
        raise NotADirectoryError(path)
    query = str(arguments.get("query") or task.goal or "")
    limit = int(arguments.get("limit") or 5)
    ranked: list[tuple[tuple[int, int, str], Path]] = []
    for child in candidate_paths(path, max_depth=2):
        score = score_match(child, query, path)
        ranked.append(((-score[0], -score[1], score[2]), child))
    ranked.sort(key=lambda item: item[0])
    items = [candidate.relative_to(path).as_posix() for _, candidate in ranked[:limit]]
    text = "\n".join(f"- {item}" for item in items)
    return build_agent_output(context=context, path=relative_workspace_path(context, path), text=text, summary=f"ranked {len(items)} entries", items=items)


def extract_workspace_outline(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    if path.suffix == ".py":
        text = path.read_text(encoding="utf-8")
        module = ast.parse(text)
        names = [node.name for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        outline = "\n".join(f"- {name}" for name in names[:20]) or "- (no top-level symbols)"
    else:
        outline = summarize_path(path)
    summary = f"outline for {path.name}"
    remember_focus(context, path, summary)
    record_evidence(task, context, source="workspace", path=path, content=outline, summary=summary)
    return build_agent_output(context=context, path=relative_workspace_path(context, path), text=outline, summary=summary)


def apply_text_patch(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    search_text = str(arguments.get("search_text") or "")
    replace_text = str(arguments.get("replace_text") or "")
    original = path.read_text(encoding="utf-8")
    if search_text not in original:
        raise ValueError("search_text not found in target file")
    updated = original.replace(search_text, replace_text, 1)
    path.write_text(updated, encoding="utf-8")
    remember_focus(context, path, f"patched {path.name}")
    rel_path = relative_workspace_path(context, path)
    if rel_path not in task.state.modified_paths:
        task.state.modified_paths.append(rel_path)
    return {**build_agent_output(context=context, path=rel_path, text=truncate_text(updated, limit=output_limit(context, "workspace_max_write_chars", 2000), label="patched file"), summary=f"Patched {path.name}", changed_paths=[rel_path]), "before_text": original, "after_text": updated}


def append_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    content = str(arguments.get("content") or "")
    original = path.read_text(encoding="utf-8")
    updated = original + content
    path.write_text(updated, encoding="utf-8")
    remember_focus(context, path, f"appended {path.name}")
    rel_path = relative_workspace_path(context, path)
    if rel_path not in task.state.modified_paths:
        task.state.modified_paths.append(rel_path)
    return build_agent_output(context=context, path=rel_path, text=truncate_text(updated, limit=output_limit(context, "workspace_max_write_chars", 2000), label="appended file"), summary=f"Appended to {path.name}", changed_paths=[rel_path])


def move_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    destination = resolve_workspace_path(context, str(arguments.get("destination_path") or ""))
    destination.parent.mkdir(parents=True, exist_ok=True)
    path.rename(destination)
    remember_focus(context, destination, f"moved to {destination.name}")
    rel_destination = relative_workspace_path(context, destination)
    task.state.modified_paths.append(rel_destination)
    return build_agent_output(context=context, path=rel_destination, text=rel_destination, summary=f"Moved to {rel_destination}", changed_paths=[rel_destination])


def delete_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    rel_path = relative_workspace_path(context, path)
    if path.is_dir():
        raise IsADirectoryError(path)
    path.unlink()
    task.state.modified_paths.append(rel_path)
    context.application_context.session_memory.remember_focus([], summary=f"deleted: {rel_path}")
    return build_agent_output(context=context, path=rel_path, text=f"Deleted {rel_path}", summary=f"Deleted {rel_path}", changed_paths=[rel_path])


def create_workspace_path(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    kind = str(arguments.get("kind") or "file").strip().lower()
    content = str(arguments.get("content") or "")
    if kind == "directory":
        path.mkdir(parents=True, exist_ok=True)
        text = f"Created directory {relative_workspace_path(context, path)}"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        text = truncate_text(content, limit=output_limit(context, "workspace_max_write_chars", 2000), label="created file")
    remember_focus(context, path, f"created {path.name}")
    rel_path = relative_workspace_path(context, path)
    task.state.modified_paths.append(rel_path)
    return build_agent_output(context=context, path=rel_path, text=text, summary=f"Created {rel_path}", changed_paths=[rel_path])


def edit_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_workspace_path(context, str(arguments.get("path") or ""))
    content = str(arguments.get("content") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    remember_focus(context, path, f"edited {path.name}")
    rel_path = relative_workspace_path(context, path)
    if rel_path not in task.state.modified_paths:
        task.state.modified_paths.append(rel_path)
    return build_agent_output(context=context, path=rel_path, text=truncate_text(content, limit=output_limit(context, "workspace_max_write_chars", 2000), label="edited file"), summary=f"Edited {rel_path}", changed_paths=[rel_path])
