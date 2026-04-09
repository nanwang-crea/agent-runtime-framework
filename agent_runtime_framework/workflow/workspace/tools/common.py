from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Any

from agent_runtime_framework.memory import MemoryRecord
from agent_runtime_framework.resources import ResourceRef


def workspace_root(context: Any) -> Path:
    roots = getattr(context.application_context.resource_repository, "allowed_roots", [])
    if not roots:
        raise RuntimeError("no allowed workspace roots configured")
    return Path(roots[0]).expanduser().resolve()


def resolve_workspace_path(context: Any, path_arg: str) -> Path:
    root = workspace_root(context)
    raw = str(path_arg or ".").strip() or "."
    candidate = Path(raw).expanduser()
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"outside allowed roots: {path}") from exc
    return path


def relative_workspace_path(context: Any, path: str | Path) -> str:
    target = Path(path).expanduser().resolve()
    root = workspace_root(context)
    try:
        return target.relative_to(root).as_posix() or "."
    except ValueError:
        return str(target)


def output_limit(context: Any, key: str, default: int) -> int:
    value = context.application_context.config.get(key, default)
    try:
        return max(80, int(value))
    except (TypeError, ValueError):
        return default


def truncate_text(text: str, *, limit: int, label: str = "output") -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit].rstrip()}\n\n[输出已截断：{label}，仅展示前 {limit} 个字符。]"


def build_agent_output(*, context: Any, path: str, text: str, summary: str, changed_paths: list[str] | None = None, items: list[str] | None = None) -> dict[str, Any]:
    payload = {
        "path": path,
        "text": text,
        "content": text,
        "summary": summary,
        "changed_paths": list(changed_paths or []),
        "items": list(items or []),
        "entities": {"path": path, "items": list(items or [])},
    }
    if path:
        payload["resolved_path"] = path
    return payload


def remember_focus(context: Any, path: Path, summary: str) -> None:
    remember = getattr(getattr(context.application_context, "index_memory", None), "remember", None)
    if callable(remember):
        rel_path = relative_workspace_path(context, path)
        remember(MemoryRecord(key=f"focus:{rel_path}", text=f"{rel_path} {summary}".strip(), kind="workspace_focus", metadata={"path": rel_path, "summary": summary}))


def record_evidence(task: Any, context: Any, *, source: str, path: Path, content: str, summary: str, kind: str = "file") -> None:
    from agent_runtime_framework.workflow.workspace.models import EvidenceItem

    rel_path = relative_workspace_path(context, path)
    task.state.evidence_items.append(EvidenceItem(source=source, kind=kind, summary=summary, path=rel_path, content=content))
    if rel_path not in task.state.read_paths:
        task.state.read_paths.append(rel_path)
    task.state.resolved_target = rel_path


def summarize_path(path: Path) -> str:
    if path.is_dir():
        children = sorted(path.iterdir(), key=lambda item: item.name)
        preview = ", ".join(child.name for child in children[:5])
        return preview or "empty directory"
    if path.suffix == ".py":
        return summarize_python_file(path)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return "binary or unreadable file"
    for line in text.splitlines():
        stripped = line.strip().strip("#")
        if stripped:
            return stripped[:120]
    return "file has minimal content"


def summarize_python_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
        module = ast.parse(text)
    except Exception:
        return "Python module"
    docstring = ast.get_docstring(module)
    if docstring:
        return docstring.strip().splitlines()[0][:120]
    symbols = [node.name for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return f"Defines {', '.join(symbols[:4])}" if symbols else "Python module"


def candidate_paths(root: Path, *, max_depth: int = 4) -> list[Path]:
    ignored = {".git", ".arf", "__pycache__", "node_modules", "dist", "build"}
    items: list[Path] = []
    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) > max_depth:
            continue
        if any(part in ignored or part.startswith(".") for part in relative.parts[:-1]):
            continue
        items.append(path)
    return items


def score_match(path: Path, query: str, root: Path) -> tuple[int, int, str]:
    relative = path.relative_to(root).as_posix().lower()
    name = path.name.lower()
    stem = path.stem.lower()
    tokens = re.findall(r"[a-z0-9_./-]+|[\u4e00-\u9fff]+", query.lower())
    score = 0
    if query.lower() == name or query.lower() == relative or query.lower() == stem:
        score += 100
    for token in tokens:
        if token == name or token == stem:
            score += 50
        elif token in name or token in stem:
            score += 30
        elif token in relative:
            score += 10
    depth = len(path.relative_to(root).parts)
    return score, -depth, relative
