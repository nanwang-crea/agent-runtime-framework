from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_runtime_framework.applications.core import ApplicationContext
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.resources import ResourceRef


DesktopActionHandler = Callable[[list[ResourceRef], ApplicationContext, str], dict[str, Any]]


class DesktopActionHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, DesktopActionHandler] = {}

    def register(self, action_name: str, handler: DesktopActionHandler) -> None:
        self._handlers[action_name] = handler

    def execute(
        self,
        action_name: str,
        *,
        resources: list[ResourceRef],
        context: ApplicationContext,
        execution_mode: str,
    ) -> dict[str, Any]:
        handler = self._handlers.get(action_name)
        if handler is None:
            return {
                "kind": action_name,
                "focused_resources": [],
                "text": "未实现的动作。",
            }
        return handler(resources, context, execution_mode)

    @classmethod
    def default(cls) -> "DesktopActionHandlerRegistry":
        registry = cls()
        registry.register("list", _handle_list)
        registry.register("read", _handle_read)
        registry.register("summarize", _handle_summarize)
        registry.register("create", _handle_create)
        registry.register("edit", _handle_edit)
        registry.register("move", _handle_move)
        registry.register("delete", _handle_delete)
        return registry


def _handle_list(resources: list[ResourceRef], context: ApplicationContext, execution_mode: str) -> dict[str, Any]:
    directory = resources[0]
    children = context.resource_repository.list_directory(directory)
    visible_children = children[:1] if execution_mode == "preview" else children
    directories = [ref for ref in visible_children if ref.kind == "directory"]
    files = [ref for ref in visible_children if ref.kind == "file"]
    return {
        "kind": "list",
        "focused_resources": [directory],
        "items": visible_children,
        "directory_name": directory.title,
        "directories": directories,
        "files": files,
        "text": _format_list_result(directory.title, directories, files, execution_mode),
    }


def _handle_read(resources: list[ResourceRef], context: ApplicationContext, execution_mode: str) -> dict[str, Any]:
    if not resources:
        return {"kind": "read", "focused_resources": [], "text": "未定位到目标资源。"}
    target = resources[0]
    if target.kind == "directory":
        raise AppError(
            code="RESOURCE_IS_DIRECTORY",
            message="目标是目录，不能直接读取为单个文件内容。",
            detail=target.location,
            stage="execute",
            retriable=True,
            suggestion="可以先列出目录内容，或指定目录下的某个文件。",
        )
    text = context.resource_repository.load_text(target)
    if execution_mode == "preview":
        text = text.splitlines()[0] if text.splitlines() else text[:120]
    return {
        "kind": "read",
        "focused_resources": [target],
        "text": text,
    }


def _handle_summarize(resources: list[ResourceRef], context: ApplicationContext, execution_mode: str) -> dict[str, Any]:
    if not resources:
        return {"kind": "summarize", "focused_resources": [], "text": "未定位到目标资源。"}
    target = resources[0]
    if target.kind == "directory":
        raise AppError(
            code="RESOURCE_IS_DIRECTORY",
            message="目标是目录，不能直接总结为单个文件内容。",
            detail=target.location,
            stage="execute",
            retriable=True,
            suggestion="可以先列出目录内容，或指定 README.md、docs 下的具体文件。",
        )
    cache_key = f"summary:{target.location}"
    cached = context.index_memory.get(cache_key)
    if cached is not None:
        summary = cached["text"]
    else:
        text = context.resource_repository.load_text(target)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        summary = "\n".join(lines[:3]) if lines else text[:300]
        context.index_memory.put(cache_key, {"text": summary})
    if execution_mode == "preview":
        summary = summary.splitlines()[0] if summary.splitlines() else summary[:120]
    return {
        "kind": "summarize",
        "focused_resources": [target],
        "text": summary,
    }


def _handle_create(_resources: list[ResourceRef], context: ApplicationContext, execution_mode: str) -> dict[str, Any]:
    plan = _require_mutation_plan(context, action="create")
    target = Path(plan["target_path"])
    target_kind = str(plan.get("target_kind") or "file")
    _assert_within_roots(target, context)
    if execution_mode == "preview":
        return {"kind": "create", "focused_resources": [], "text": str(plan.get("preview") or "")}
    if target.exists():
        raise FileExistsError(str(target))
    _record_rollback_checkpoint(context, action="create", plan=plan)
    try:
        if target_kind == "directory":
            target.mkdir(parents=True, exist_ok=False)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(plan.get("after_text") or ""), encoding="utf-8")
        _maybe_fail_after_apply(context, action="create")
    except Exception as exc:
        if target.exists():
            if target.is_dir():
                target.rmdir()
            else:
                target.unlink()
        raise _mutation_failure_error("create", target, exc)
    ref = ResourceRef.for_path(target)
    return {
        "kind": "create",
        "focused_resources": [ref],
        "target_path": str(target),
        "destination_path": "",
        "rollback": {"kind": "delete_path", "path": str(target), "target_kind": target_kind},
        "text": f"{plan.get('summary')}\n\n{plan.get('diff')}",
    }


def _handle_edit(resources: list[ResourceRef], context: ApplicationContext, execution_mode: str) -> dict[str, Any]:
    plan = _require_mutation_plan(context, action="edit")
    target = Path(plan["target_path"])
    _assert_within_roots(target, context)
    if execution_mode == "preview":
        return {"kind": "edit", "focused_resources": resources, "text": str(plan.get("preview") or "")}
    if not target.exists():
        raise FileNotFoundError(str(target))
    _record_rollback_checkpoint(context, action="edit", plan=plan)
    original = str(plan.get("before_text") or "")
    try:
        target.write_text(str(plan.get("after_text") or ""), encoding="utf-8")
        _maybe_fail_after_apply(context, action="edit")
    except Exception as exc:
        target.write_text(original, encoding="utf-8")
        raise _mutation_failure_error("edit", target, exc)
    ref = ResourceRef.for_path(target)
    return {
        "kind": "edit",
        "focused_resources": [ref],
        "target_path": str(target),
        "destination_path": "",
        "rollback": {"kind": "restore_text", "path": str(target), "content": original},
        "text": f"{plan.get('summary')}\n\n{plan.get('diff')}",
    }


def _handle_move(resources: list[ResourceRef], context: ApplicationContext, execution_mode: str) -> dict[str, Any]:
    plan = _require_mutation_plan(context, action="move")
    source = Path(plan["target_path"])
    destination = Path(plan["destination_path"])
    _assert_within_roots(source, context)
    _assert_within_roots(destination, context)
    if execution_mode == "preview":
        return {"kind": "move", "focused_resources": resources, "text": str(plan.get("preview") or "")}
    if not source.exists():
        raise FileNotFoundError(str(source))
    _record_rollback_checkpoint(context, action="move", plan=plan)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        _maybe_fail_after_apply(context, action="move")
    except Exception as exc:
        if destination.exists() and not source.exists():
            destination.rename(source)
        raise _mutation_failure_error("move", source, exc)
    ref = ResourceRef.for_path(destination)
    return {
        "kind": "move",
        "focused_resources": [ref],
        "target_path": str(source),
        "destination_path": str(destination),
        "rollback": {"kind": "move_path", "from_path": str(destination), "to_path": str(source)},
        "text": f"{plan.get('summary')}\n\n{plan.get('diff')}",
    }


def _handle_delete(resources: list[ResourceRef], context: ApplicationContext, execution_mode: str) -> dict[str, Any]:
    plan = _require_mutation_plan(context, action="delete")
    target = Path(plan["target_path"])
    _assert_within_roots(target, context)
    if execution_mode == "preview":
        return {"kind": "delete", "focused_resources": resources, "text": str(plan.get("preview") or "")}
    _record_rollback_checkpoint(context, action="delete", plan=plan)
    original = str(plan.get("before_text") or "")
    if target.exists():
        if target.is_dir():
            raise IsADirectoryError(str(target))
        try:
            target.unlink()
            _maybe_fail_after_apply(context, action="delete")
        except Exception as exc:
            target.write_text(original, encoding="utf-8")
            raise _mutation_failure_error("delete", target, exc)
    return {
        "kind": "delete",
        "focused_resources": [],
        "target_path": str(target),
        "destination_path": "",
        "rollback": {"kind": "restore_text", "path": str(target), "content": original},
        "text": f"{plan.get('summary')}\n\n{plan.get('diff')}",
    }


def _require_mutation_plan(context: ApplicationContext, *, action: str) -> dict[str, Any]:
    plan = context.services.get("_current_mutation_plan")
    if not isinstance(plan, dict):
        raise AppError(
            code="MUTATION_PLAN_MISSING",
            message="缺少文件变更计划，无法执行写操作。",
            stage="plan",
            retriable=True,
            suggestion="请重试一次，让助手先生成变更预览。",
        )
    if str(plan.get("action") or "") != action:
        raise AppError(
            code="MUTATION_PLAN_MISMATCH",
            message="变更计划与当前动作不一致。",
            stage="plan",
            retriable=True,
            suggestion="请重新发起这次写操作。",
        )
    return plan


def _assert_within_roots(path: Path, context: ApplicationContext) -> None:
    resolved = path.expanduser().resolve()
    roots = [Path(root).expanduser().resolve() for root in context.resource_repository.allowed_roots]
    if any(resolved == root or root in resolved.parents for root in roots):
        return
    raise ValueError(f"path is outside allowed roots: {resolved}")


def _record_rollback_checkpoint(context: ApplicationContext, *, action: str, plan: dict[str, Any]) -> None:
    store = getattr(context, "artifact_store", None)
    if store is None or not hasattr(store, "add"):
        return
    target = str(plan.get("target_path") or "")
    destination = str(plan.get("destination_path") or "")
    summary = f"rollback checkpoint for {action}: {target}"
    if destination:
        summary = f"{summary} -> {destination}"
    store.add(
        "rollback_checkpoint",
        title=f"{action}_checkpoint",
        content=summary,
        metadata={
            "action": action,
            "target_path": target,
            "destination_path": destination,
        },
    )


def _maybe_fail_after_apply(context: ApplicationContext, *, action: str) -> None:
    marker = context.services.get("mutation_fail_after_apply")
    if marker in {action, "*"}:
        raise RuntimeError(f"forced post-apply failure for {action}")


def _mutation_failure_error(action: str, target: Path, exc: Exception) -> AppError:
    return AppError(
        code="MUTATION_EXECUTION_FAILED",
        message=f"{action} 执行失败，已尝试回滚。",
        detail=f"{target}: {type(exc).__name__}: {exc}",
        stage="execute",
        retriable=True,
        suggestion="请检查路径和文件状态后重试。",
    )


def _format_list_result(
    directory_name: str,
    directories: list[ResourceRef],
    files: list[ResourceRef],
    execution_mode: str,
) -> str:
    total = len(directories) + len(files)
    if total == 0:
        return f"`{directory_name}` 下面当前是空的。"

    parts = [f"`{directory_name}` 下面一共有 {total} 项内容。"]
    if directories:
        parts.append(f"目录：{', '.join(ref.title for ref in directories)}。")
    if files:
        parts.append(f"文件：{', '.join(ref.title for ref in files)}。")
    if execution_mode == "preview" and total > 1:
        parts.append("当前是预览模式，只展示了前面的内容。")
    return "\n".join(parts)
