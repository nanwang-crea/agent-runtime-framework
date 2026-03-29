from __future__ import annotations

import ast
import json
from pathlib import Path
import re
from typing import Any

from agent_runtime_framework.core.specs import ToolSpec
from agent_runtime_framework.agents.codex.prompting import build_codex_system_prompt, build_follow_up_context, extract_json_block, render_codex_prompt_doc
from agent_runtime_framework.agents.codex.run_context import update_loaded_instructions
from agent_runtime_framework.memory import MemoryRecord
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.resources import ResolveHint, ResolveRequest, ResourceRef, describe_resource_semantics
from agent_runtime_framework.sandbox import run_sandboxed_command

_TOOL_ASSETS_DIR = Path(__file__).with_name("tool_assets")


def _output_limit(context: Any, key: str, default: int) -> int:
    value = context.application_context.config.get(key, default)
    try:
        return max(80, int(value))
    except (TypeError, ValueError):
        return default


def _truncate_text(text: str, *, limit: int, label: str = "output") -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit].rstrip()}\n\n[{label} truncated — showing first {limit} characters.]"


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
            name="read_workspace_excerpt",
            description="Read a short excerpt from a text file inside the current workspace.",
            executor=_read_workspace_excerpt,
            input_schema={"path": "string", "max_lines": "integer"},
            permission_level="content_read",
            prompt_asset_path=str(_TOOL_ASSETS_DIR / "read_workspace_excerpt.md"),
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
            prompt_guidelines=["Use inspect_workspace_path after a directory listing when you need structure only, not deep file semantics."],
        ),
        ToolSpec(
            name="rank_workspace_entries",
            description="Rank representative files for repository overview or directory explanation.",
            executor=_rank_workspace_entries,
            input_schema={"path": "string", "query": "string"},
            permission_level="metadata_read",
            prompt_snippet="Select the most representative files under a directory.",
            prompt_guidelines=["Use rank_workspace_entries after structure inspection to choose which files deserve deeper reading."],
        ),
        ToolSpec(
            name="extract_workspace_outline",
            description="Extract a concise role-oriented outline for a workspace file.",
            executor=_extract_workspace_outline,
            input_schema={"path": "string"},
            permission_level="content_read",
            prompt_snippet="Extract symbols, entrypoints, or short role summaries from a file.",
            prompt_guidelines=["Use extract_workspace_outline on representative files before writing a repository overview."],
        ),
        ToolSpec(
            name="replace_workspace_text",
            description="Replace a known text fragment in a workspace file.",
            executor=_replace_workspace_text,
            input_schema={"path": "string", "search_text": "string", "replace_text": "string"},
            permission_level="safe_write",
            prompt_asset_path=str(_TOOL_ASSETS_DIR / "replace_workspace_text.md"),
            serialize_by_argument="path",
        ),
        ToolSpec(
            name="append_workspace_text",
            description="Append text to the end of a workspace file.",
            executor=_append_workspace_text,
            input_schema={"path": "string", "content": "string"},
            permission_level="safe_write",
            prompt_asset_path=str(_TOOL_ASSETS_DIR / "append_workspace_text.md"),
            serialize_by_argument="path",
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
        ToolSpec(
            name="grep_workspace",
            description="Search for a text pattern across all workspace files, returning file paths, line numbers, and surrounding context lines.",
            executor=_grep_workspace,
            input_schema={"pattern": "string", "path": "string", "context_lines": "integer", "file_glob": "string"},
            permission_level="content_read",
            prompt_snippet="Full-text search across workspace files with context.",
            prompt_guidelines=[
                "Use grep_workspace to find usages, definitions, or patterns across multiple files.",
                "Prefer grep_workspace over read_workspace_text when you need to locate where something is defined or used.",
            ],
        ),
        ToolSpec(
            name="search_workspace_symbols",
            description="Search for function definitions, class declarations, or variable assignments by symbol name across the workspace.",
            executor=_search_workspace_symbols,
            input_schema={"symbol": "string", "path": "string", "kind": "string"},
            permission_level="content_read",
            prompt_snippet="Find where a function, class, or variable is defined in the workspace.",
            prompt_guidelines=[
                "Use search_workspace_symbols before modifying a function to understand its definition and all call sites.",
                "kind can be 'function', 'class', 'variable', or 'all' (default).",
            ],
        ),
        ToolSpec(
            name="get_git_diff",
            description="Get the current git diff showing all uncommitted changes in the workspace, or the diff for a specific file.",
            executor=_get_git_diff,
            input_schema={"path": "string", "staged": "boolean"},
            permission_level="metadata_read",
            prompt_snippet="Show uncommitted git changes in the workspace.",
            prompt_guidelines=[
                "Use get_git_diff to understand what has already been changed before making further edits.",
                "Use get_git_diff after edits to confirm the diff looks correct before running tests.",
            ],
        ),
        ToolSpec(
            name="run_tests",
            description="Run the project test suite (or a specific test file/pattern) and return a structured summary of pass/fail results with failure details.",
            executor=_run_tests,
            input_schema={"path": "string", "pattern": "string", "timeout": "integer"},
            permission_level="safe_write",
            timeout_seconds=120.0,
            prompt_snippet="Run tests and return pass/fail summary.",
            prompt_guidelines=[
                "Use run_tests after any code change to verify correctness.",
                "Prefer run_tests over run_shell_command for test execution — it returns structured results.",
                "If run_tests fails, read the failure details and fix the root cause before retrying.",
            ],
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
    update_loaded_instructions(context, str(ref.location))
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
                next_hint="Next: decide whether to list the directory, inspect it, or read a file depending on the target type.",
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
                next_hint="Next: decide whether to list the directory, inspect it, or read a file depending on the target type.",
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
                next_hint="Next: decide whether to list the directory, inspect it, or read a file depending on the target type.",
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
        text = "Multiple possible targets found, please specify one:\n" + "\n".join(f"- {item}" for item in candidate_paths[:6])
        return {
            **_build_agent_output(
                path="",
                text=text,
                summary="Found multiple possible targets.",
                next_hint="Specify one of the candidate paths or names directly.",
                items=candidate_paths[:12],
            ),
            "resolved_path": "",
            "best_match": "",
            "candidates": candidate_paths[:20],
            "resolution_status": "ambiguous",
            "resolution_source": state.source,
        }
    text = "No clear target found. Please provide a more specific path, filename, or module name."
    return {
        **_build_agent_output(
            path="",
            text=text,
            summary="No clear target found.",
            next_hint="Add a path or a more specific name under the current directory.",
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
    lines = [f"Found {len(items)} entries."]
    if directories:
        lines.append(f"Directories: {', '.join(directories)}")
    if files:
        lines.append(f"Files: {', '.join(files)}")
    text = "\n".join(lines)
    _remember_focus(context, ref, text)
    return _build_agent_output(
        path=ref.location,
        text=text,
        summary=lines[0],
        next_hint="If you need to understand directory responsibilities, call inspect_workspace_path next.",
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
                        content=build_codex_system_prompt(
                            render_codex_prompt_doc("target_resolver_system")
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=render_codex_prompt_doc(
                            "target_resolver_user",
                            query=query,
                            follow_up_context=build_follow_up_context(session=None, context=context) or "Recent turns:\n(none)",
                            target_hint=target_hint,
                            candidate_paths=json.dumps(candidates[:80]),
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
        parsed = json.loads(extract_json_block(str(response.content or "")))
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
    if any(marker in lowered_query for marker in ("当前目录", "根目录", "workspace", "current directory", "root")):
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
        next_hint="If more content is needed, continue reading the same path or switch to summarize/inspect.",
    )


def _read_workspace_excerpt(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
    )
    content = context.application_context.resource_repository.load_text(ref)
    try:
        max_lines = max(1, int(arguments.get("max_lines") or 12))
    except (TypeError, ValueError):
        max_lines = 12
    excerpt = "\n".join(content.splitlines()[:max_lines]).strip()
    if not excerpt:
        excerpt = content[: _output_limit(context, "codex_max_summary_chars", 1000)].strip()
    excerpt = _truncate_text(excerpt, limit=_output_limit(context, "codex_max_summary_chars", 1000), label="excerpt")
    summary = "\n".join(line.strip() for line in excerpt.splitlines()[:2] if line.strip()) or f"Excerpt of {Path(ref.location).name}"
    _remember_focus(context, ref, summary)
    return _build_agent_output(
        path=ref.location,
        text=excerpt,
        summary=summary,
        truncated=excerpt != content.strip(),
        next_hint="If the excerpt is insufficient, read the full file or extract a structured outline.",
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
    summary = _truncate_text(summary, limit=_output_limit(context, "codex_max_summary_chars", 1000), label="summary")
    _remember_focus(context, ref, summary)
    return _build_agent_output(
        path=ref.location,
        text=summary,
        summary=summary,
        truncated=summary != content[: len(summary)],
        next_hint="If the summary is insufficient, read the original file or inspect the related path.",
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
            next_hint="If you need the raw file content, read that file next.",
        )
    items = context.application_context.resource_repository.list_directory(ref)
    directories = [item for item in items if item.kind == "directory"]
    files = [item for item in items if item.kind == "file"]
    lines = [f"{path.name or str(path)}: {len(items)} entries."]
    if directories:
        lines.append("Subdirectories:")
        for item in directories[:8]:
            lines.append(f"- {item.title}/")
    if files:
        lines.append("Files:")
        for item in files[:8]:
            file_path = Path(item.location)
            lines.append(f"- {item.title} ({_structure_label_for_file(file_path)})")
    text = "\n".join(lines)
    text = _truncate_text(text, limit=_output_limit(context, "codex_max_inspect_chars", 2000))
    _remember_focus(context, ref, text)
    return _build_agent_output(
        path=str(path),
        text=text,
        summary=lines[0],
        next_hint="If module roles need more detail, continue reading key files.",
        items=[item.title for item in items],
    )


def _rank_workspace_entries(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
        use_default_directory=not bool(arguments.get("path")),
    )
    path = Path(ref.location)
    if path.is_file():
        relative = _relative_workspace_path(context, str(path))
        summary = f"Representative file: {relative}"
        _remember_focus(context, ref, summary)
        return {
            **_build_agent_output(path=str(path), text=summary, summary=summary, next_hint="Next: extract the outline of this file.", items=[relative]),
            "ranked_paths": [relative],
        }
    ranked = _rank_representative_files(path, query=str(arguments.get("query") or ""), context=context)
    relative_ranked = [_relative_workspace_path(context, str(item)) for item in ranked]
    lines = ["Representative file candidates:"]
    for candidate in ranked[:5]:
        relative = _relative_workspace_path(context, str(candidate))
        lines.append(f"- {relative}: {_summarize_file(candidate)}")
    text = "\n".join(lines)
    _remember_focus(context, ref, text)
    return {
        **_build_agent_output(
            path=str(path),
            text=text,
            summary="Representative file selected.",
            next_hint="Next: extract outlines of these representative files or read the entry point.",
            items=relative_ranked,
        ),
        "ranked_paths": relative_ranked,
    }


def _extract_workspace_outline(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    ref = _resolve_resource_ref(
        context,
        str(arguments.get("path") or ""),
        use_last_focus=bool(arguments.get("use_last_focus")),
    )
    path = Path(ref.location)
    relative = _relative_workspace_path(context, str(path))
    detail = _outline_file(path)
    text = f"- {relative}：{detail}"
    _remember_focus(context, ref, text)
    return _build_agent_output(
        path=str(path),
        text=text,
        summary=detail,
        next_hint="If more detail is needed, continue reading the full file or select the next representative file.",
        items=[relative],
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
    result["next_hint"] = "If this is a verification command, check the success field; otherwise use context to decide whether to continue reading."
    result["changed_paths"] = []
    result["entities"] = {"path": "", "items": []}
    return result


def _summarize_file(path: Path) -> str:
    if not path.exists():
        return "File does not exist."
    if path.suffix == ".py":
        return _summarize_python_file(path)
    text = _read_text_for_summary(path)
    if text is None:
        return "Binary or non-UTF-8 file."
    for line in text.splitlines():
        stripped = line.strip().strip("#").strip()
        if stripped:
            return stripped[:120]
    return "File has minimal content."


def _summarize_python_file(path: Path) -> str:
    text = _read_text_for_summary(path)
    if text is None:
        return "Python file (unreadable as UTF-8)."
    try:
        module = ast.parse(text)
    except SyntaxError:
        return "Python module."
    docstring = ast.get_docstring(module)
    if docstring:
        return docstring.strip().splitlines()[0][:120]
    symbols = [node.name for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    if symbols:
        return f"Defines {', '.join(symbols[:4])}"
    return "Python module."


def _outline_file(path: Path) -> str:
    if not path.exists():
        return "file not found"
    if path.suffix == ".py":
        return _outline_python_file(path)
    return _summarize_file(path)


def _outline_python_file(path: Path) -> str:
    text = _read_text_for_summary(path)
    if text is None:
        return "Python file (unreadable as UTF-8)."
    try:
        module = ast.parse(text)
    except SyntaxError:
        return "Python file."
    docstring = ast.get_docstring(module)
    symbols = [node.name for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    if docstring and symbols:
        return f"{docstring.strip().splitlines()[0][:80]}; defines {', '.join(symbols[:4])}"
    if docstring:
        return docstring.strip().splitlines()[0][:120]
    if symbols:
        return f"Defines {', '.join(symbols[:4])}"
    return "Python file."


def _read_text_for_summary(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _structure_label_for_file(path: Path) -> str:
    if _read_text_for_summary(path) is None:
        return "binary"
    return "text"


def _rank_representative_files(root: Path, *, query: str, context: Any, limit: int = 4) -> list[Path]:
    candidates: list[tuple[tuple[int, int, str], Path]] = []
    query_tokens = {token.lower() for token in re.split(r"[\s/_.\-，。,:：]+", query) if len(token.strip()) >= 2}
    for file_path in _iter_candidate_files(root, max_depth=2):
        relative = file_path.relative_to(root).as_posix()
        name = file_path.name.lower()
        score = 0
        if name in {"readme.md", "readme", "pyproject.toml", "package.json", "cargo.toml"}:
            score += 100
        if name in {"__init__.py", "main.py", "app.py", "index.ts", "index.tsx", "service.py"}:
            score += 90
        if file_path.suffix in {".py", ".md", ".ts", ".tsx", ".js", ".json", ".toml"}:
            score += 20
        if any(token in relative.lower() for token in query_tokens):
            score += 30
        depth = len(file_path.relative_to(root).parts)
        score -= max(0, depth - 1) * 5
        candidates.append(((-score, depth, relative), file_path))
    candidates.sort(key=lambda item: item[0])
    return [path for _, path in candidates[:limit]]


def _iter_candidate_files(root: Path, *, max_depth: int) -> list[Path]:
    ignored_dirs = {"__pycache__", ".git", "node_modules", "dist", "build", ".arf"}
    results: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) > max_depth:
            continue
        if any(part in ignored_dirs or part.startswith(".") for part in relative.parts[:-1]):
            continue
        results.append(path)
    return results


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
            next_hint="Next: run verification or re-read the file to confirm the change.",
            changed_paths=[path.name],
        ),
        "before_text": original,
        "after_text": updated,
    }


def _replace_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    return _apply_text_patch(task, context, arguments)


def _append_workspace_text(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_workspace_path(context, str(arguments.get("path") or ""))
    if not path.exists():
        raise FileNotFoundError(path)
    content = str(arguments.get("content") or "")
    if not content:
        raise ValueError("missing content")
    original = path.read_text(encoding="utf-8")
    updated = original + content
    path.write_text(updated, encoding="utf-8")
    ref = ResourceRef.for_path(path)
    summary = "\n".join(updated.splitlines()[-3:]) if updated.strip() else ""
    _remember_focus(context, ref, summary)
    truncated = _truncate_text(updated, limit=_output_limit(context, "codex_max_write_chars", 2000))
    return {
        **_build_agent_output(
            path=str(path),
            text=truncated,
            summary=f"Appended to {path.name}",
            truncated=truncated != updated.strip(),
            next_hint="Next: run verification or re-read the file to confirm the appended content.",
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
            next_hint="If you need to confirm the content is unchanged, read the target file next.",
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
        next_hint="If this is a planned cleanup, next run verification or continue the remaining edits.",
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
            next_hint="If this is a code file, read or verify its content next.",
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
            next_hint="Next: run verification or re-read the file to confirm the final content.",
            changed_paths=[path.name],
        ),
        "content": content,
    }


def _grep_workspace(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    import subprocess
    pattern = str(arguments.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern is required")
    root = _workspace_root(context)
    search_path = root
    path_arg = str(arguments.get("path") or "").strip()
    if path_arg:
        search_path = _resolve_workspace_path(context, path_arg)
    context_lines = int(arguments.get("context_lines") or 2)
    context_lines = max(0, min(context_lines, 10))
    file_glob = str(arguments.get("file_glob") or "").strip()
    cmd = ["grep", "-rn", f"--context={context_lines}", "--include=*.py", "--include=*.ts", "--include=*.tsx", "--include=*.js", "--include=*.md"]
    if file_glob:
        cmd = ["grep", "-rn", f"--context={context_lines}", f"--include={file_glob}"]
    cmd += [pattern, str(search_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, cwd=str(root))
        output = result.stdout
    except subprocess.TimeoutExpired:
        output = f"[search timed out]"
    # Make paths relative
    root_str = str(root) + "/"
    lines = []
    for line in output.splitlines():
        if line.startswith(root_str):
            line = line[len(root_str):]
        lines.append(line)
    text = "\n".join(lines)
    limit = _output_limit(context, "codex_max_grep_chars", 4000)
    truncated_text = _truncate_text(text, limit=limit, label="search results")
    match_count = output.count("\n") if output else 0
    summary = f"Found {match_count} lines matching '{pattern}'"
    return {
        **_build_agent_output(
            path=str(search_path),
            text=truncated_text,
            summary=summary,
            truncated=truncated_text != text,
            next_hint="After locating the target file from the search results, use read_workspace_text to read the full content.",
        ),
        "pattern": pattern,
        "match_count": match_count,
    }


def _search_workspace_symbols(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    import subprocess
    symbol = str(arguments.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("symbol is required")
    root = _workspace_root(context)
    search_path = root
    path_arg = str(arguments.get("path") or "").strip()
    if path_arg:
        search_path = _resolve_workspace_path(context, path_arg)
    kind = str(arguments.get("kind") or "all").strip().lower()
    # Build patterns for different symbol kinds
    patterns: list[str] = []
    if kind in ("function", "all"):
        patterns += [rf"def {re.escape(symbol)}\b", rf"function {re.escape(symbol)}\b", rf"const {re.escape(symbol)}\s*=", rf"let {re.escape(symbol)}\s*=", rf"var {re.escape(symbol)}\s*="]
    if kind in ("class", "all"):
        patterns += [rf"class {re.escape(symbol)}\b"]
    if kind in ("variable", "all") and kind != "all":
        patterns += [rf"{re.escape(symbol)}\s*="]
    results: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        cmd = ["grep", "-rn", "-E", "--include=*.py", "--include=*.ts", "--include=*.tsx", "--include=*.js", pat, str(search_path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=str(root))
            for line in result.stdout.splitlines():
                rel = line
                if line.startswith(str(root) + "/"):
                    rel = line[len(str(root)) + 1:]
                if rel not in seen:
                    seen.add(rel)
                    results.append(rel)
        except subprocess.TimeoutExpired:
            pass
    text = "\n".join(results[:80])
    if not text:
        text = f"Symbol '{symbol}' not found."
    summary = f"Found {len(results)} definition(s) of '{symbol}'"
    return {
        **_build_agent_output(
            path=str(search_path),
            text=text,
            summary=summary,
            next_hint="After finding the definition, use read_workspace_text to confirm context, or grep_workspace to find all call sites.",
        ),
        "symbol": symbol,
        "match_count": len(results),
    }


def _get_git_diff(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    import subprocess
    root = _workspace_root(context)
    staged = bool(arguments.get("staged") or False)
    path_arg = str(arguments.get("path") or "").strip()
    cmd = ["git", "diff"]
    if staged:
        cmd.append("--staged")
    if path_arg:
        target = _resolve_workspace_path(context, path_arg)
        cmd += ["--", str(target)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, cwd=str(root))
        diff_text = result.stdout
        if result.returncode != 0 and result.stderr:
            diff_text = f"[git error] {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        diff_text = "[git diff timed out]"
    except FileNotFoundError:
        diff_text = "[git not found in PATH]"
    if not diff_text.strip():
        diff_text = "(no changes — working tree is clean)"
    limit = _output_limit(context, "codex_max_diff_chars", 6000)
    truncated_text = _truncate_text(diff_text, limit=limit, label="diff")
    summary = f"Git diff ({('staged' if staged else 'unstaged')}): {len(diff_text.splitlines())} lines"
    return {
        **_build_agent_output(
            path=str(root),
            text=truncated_text,
            summary=summary,
            truncated=truncated_text != diff_text.strip(),
            next_hint="If the diff looks correct, use run_tests to verify the changes.",
        ),
        "staged": staged,
        "line_count": len(diff_text.splitlines()),
    }


def _run_tests(task: Any, context: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    import subprocess
    root = _workspace_root(context)
    path_arg = str(arguments.get("path") or "").strip()
    pattern = str(arguments.get("pattern") or "").strip()
    timeout = int(arguments.get("timeout") or 60)
    timeout = max(10, min(timeout, 120))
    # Auto-detect test runner
    cmd: list[str] = []
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "pytest.ini").exists():
        cmd = ["python", "-m", "pytest", "--tb=short", "-q"]
        if path_arg:
            cmd.append(path_arg)
        if pattern:
            cmd += ["-k", pattern]
    elif (root / "package.json").exists():
        cmd = ["npm", "test", "--", "--watchAll=false"]
        if pattern:
            cmd += ["--testNamePattern", pattern]
    else:
        cmd = ["python", "-m", "pytest", "--tb=short", "-q"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root))
        output = (result.stdout + result.stderr).strip()
        passed = result.returncode == 0
    except subprocess.TimeoutExpired:
        output = f"[tests timed out after {timeout}s]"
        passed = False
    except FileNotFoundError as exc:
        output = f"[test runner not found: {exc}]"
        passed = False
    limit = _output_limit(context, "codex_max_test_chars", 5000)
    truncated_output = _truncate_text(output, limit=limit, label="test output")
    status_label = "PASSED" if passed else "FAILED"
    summary = f"Tests {status_label}: {len(output.splitlines())} lines of output"
    return {
        **_build_agent_output(
            path=str(root),
            text=truncated_output,
            summary=summary,
            truncated=truncated_output != output,
            next_hint=("All tests passed." if passed else "Tests failed — read the error output and fix the root cause before re-running."),
        ),
        "passed": passed,
        "return_code": result.returncode if 'result' in dir() else -1,
    }
