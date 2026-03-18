from __future__ import annotations

from collections.abc import Callable
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
