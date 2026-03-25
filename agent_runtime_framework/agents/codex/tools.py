from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from agent_runtime_framework.core.specs import ToolSpec
from agent_runtime_framework.memory import MemoryRecord
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.resources import ResolveHint, ResolveRequest, ResourceRef, describe_resource_semantics
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
            name="resolve_workspace_target",
            description="Resolve a natural-language workspace target into the best matching path and candidate list.",
            executor=_resolve_workspace_target,
            input_schema={"query": "string", "target_hint": "string"},
            permission_level="metadata_read",
            prompt_snippet="Resolve a fuzzy workspace target before exploring files or directories.",
            prompt_guidelines=["Use resolve_workspace_target first when the user mentions a folder/file informally or ambiguously."],
        ),
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
    index_memory = getattr(context.application_context, "index_memory", None)
    remember = getattr(index_memory, "remember", None)
    if not callable(remember):
        return
    path = _relative_workspace_path(context, ref.location)
    remember(
        MemoryRecord(
            key=f"focus:{path}",
            text=f"{path} {summary}".strip(),
            kind="workspace_focus",
            metadata={"path": path, "summary": summary},
        )
    )


def _resolve_workspace_target(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    target_hint = str(arguments.get("target_hint") or "").strip()
    root = _workspace_root(context)
    candidates = _workspace_candidates(root)
    best_match = _resolve_target_with_model(query, target_hint, candidates, context)
    if best_match:
        resolved = root if best_match in {"", "."} else (root / best_match).resolve()
        ref = ResourceRef.for_path(resolved)
        semantics = describe_resource_semantics(ref, context.application_context.resource_repository)
        summary = f"Resolved target: {best_match or '.'}"
        _remember_focus(context, ref, summary)
        return {
            **_build_agent_output(
                path=str(resolved),
                text=summary,
                summary=summary,
                next_hint="下一步根据目标类型决定是列目录、inspect，还是读取文件。",
                items=candidates[:12],
            ),
            "resolved_path": str(resolved),
            "resource_kind": semantics.resource_kind,
            "is_container": semantics.is_container,
            "allowed_actions": list(semantics.allowed_actions),
            "best_match": best_match or ".",
            "candidates": candidates[:20],
            "resolution_status": "resolved",
            "resolution_source": "model",
        }
    state = _resolve_target_state(query, target_hint, context)
    if state is None:
        best_match = _resolve_target_fallback(query, target_hint, candidates)
        resolved = root if best_match in {"", "."} else (root / best_match).resolve()
        ref = ResourceRef.for_path(resolved)
        semantics = describe_resource_semantics(ref, context.application_context.resource_repository)
        summary = f"Resolved target: {best_match or '.'}"
        _remember_focus(context, ref, summary)
        return {
            **_build_agent_output(
                path=str(resolved),
                text=summary,
                summary=summary,
                next_hint="下一步根据目标类型决定是列目录、inspect，还是读取文件。",
                items=candidates[:12],
            ),
            "resolved_path": str(resolved),
            "resource_kind": semantics.resource_kind,
            "is_container": semantics.is_container,
            "allowed_actions": list(semantics.allowed_actions),
            "best_match": best_match or ".",
            "candidates": candidates[:20],
            "resolution_status": "resolved",
            "resolution_source": "fallback",
        }
    if state.status == "resolved" and state.selected is not None:
        relative_path = _relative_workspace_path(context, state.selected.ref.location)
        resolved = root if relative_path == "." else (root / relative_path).resolve()
        summary = f"Resolved target: {relative_path or '.'}"
        _remember_focus(context, state.selected.ref, summary)
        return {
            **_build_agent_output(
                path=str(resolved),
                text=summary,
                summary=summary,
                next_hint="下一步根据目标类型决定是列目录、inspect，还是读取文件。",
                items=candidates[:12],
            ),
            "resolved_path": str(resolved),
            "resource_kind": state.selected.resource_kind,
            "is_container": state.selected.is_container,
            "allowed_actions": list(state.selected.allowed_actions),
            "best_match": relative_path or ".",
            "candidates": candidates[:20],
            "resolution_status": "resolved",
            "resolution_source": state.source,
        }
    if state.status == "ambiguous":
        candidate_paths = [_relative_workspace_path(context, item.ref.location) for item in state.candidates]
        text = "找到多个可能目标，请明确指定其中一个：\n" + "\n".join(f"- {item}" for item in candidate_paths[:6])
        return {
            **_build_agent_output(
                path="",
                text=text,
                summary="找到多个可能目标。",
                next_hint="请直接指定其中一个候选路径或名称。",
                items=candidate_paths[:12],
            ),
            "resolved_path": "",
            "best_match": "",
            "candidates": candidate_paths[:20],
            "resolution_status": "ambiguous",
            "resolution_source": state.source,
        }
    text = "没有找到明确目标。请补充更具体的路径、文件名或模块名。"
    return {
        **_build_agent_output(
            path="",
            text=text,
            summary="没有找到明确目标。",
            next_hint="可以补充路径，或从当前目录下给出更具体的名称。",
            items=candidates[:12],
        ),
        "resolved_path": "",
        "best_match": "",
        "candidates": candidates[:20],
        "resolution_status": "unresolved",
        "resolution_source": state.source or "fallback",
    }


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


def _workspace_candidates(root: Path) -> list[str]:
    candidates = ["."]
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        candidates.append(relative)
    return candidates[:400]


def _relative_workspace_path(context: Any, path: str) -> str:
    root = _workspace_root(context)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except FileNotFoundError:
        resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return path.strip()
    return relative or "."


def _resolve_target_with_model(query: str, target_hint: str, candidates: list[str], context: Any) -> str:
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        return ""
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "你是 workspace target resolver。"
                            "只输出 JSON，格式为 {\"best_match\":\"...\",\"candidates\":[...]}。"
                            "best_match 必须从候选列表中选择，若当前目录最合适则返回 ."
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"用户问题：{query}\n"
                            f"目标提示：{target_hint}\n"
                            f"候选路径：{json.dumps(candidates[:80], ensure_ascii=False)}"
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=180,
            ),
        )
    except Exception:
        return ""
    try:
        parsed = json.loads(str(response.content or "").strip())
    except Exception:
        return ""
    best_match = str(parsed.get("best_match") or "").strip()
    return best_match if best_match in set(candidates) else ""


def _resolve_target_state(query: str, target_hint: str, context: Any):
    resolver = getattr(context.application_context, "resource_resolver", None)
    if resolver is None or not hasattr(resolver, "resolve_state"):
        return None
    repository = context.application_context.resource_repository
    default_directory = ResourceRef.for_path(_workspace_root(context))
    snapshot = context.application_context.session_memory.snapshot()
    request = ResolveRequest(
        user_input=query,
        target_hint=target_hint,
        default_directory=default_directory,
        last_focused=list(snapshot.focused_resources),
        memory_hints=_memory_hints_for_query(query, target_hint, context),
    )
    return resolver.resolve_state(request, repository)


def _memory_hints_for_query(query: str, target_hint: str, context: Any) -> list[ResolveHint]:
    index_memory = getattr(context.application_context, "index_memory", None)
    search = getattr(index_memory, "search", None)
    if not callable(search):
        return []
    memory_query = " ".join(part for part in (target_hint, query) if part).strip()
    if not memory_query:
        return []
    hints: list[ResolveHint] = []
    seen: set[str] = set()
    for kind in ("workspace_focus", "task_conclusion", "workspace_fact"):
        for record in search(memory_query, limit=5, kind=kind):
            path = str(record.metadata.get("path") or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            hints.append(ResolveHint(path=path, source=kind, summary=str(record.metadata.get("summary") or "")))
    return hints


def _resolve_target_fallback(query: str, target_hint: str, candidates: list[str]) -> str:
    lowered_query = query.lower()
    lowered_hint = target_hint.lower()
    if any(marker in lowered_query for marker in ("当前目录", "根目录", "workspace")):
        return "."
    for candidate in candidates:
        base = Path(candidate).name.lower()
        if lowered_hint and lowered_hint in base:
            return candidate
        if lowered_hint and lowered_hint == candidate.lower():
            return candidate
    for candidate in candidates:
        base = Path(candidate).name.lower()
        if base and base in lowered_query:
            return candidate
    return "."


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
