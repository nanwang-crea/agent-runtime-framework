from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory, MarkdownIndexMemory, WorkingMemory
from agent_runtime_framework.observability import InMemoryRunObserver, RunObserver
from agent_runtime_framework.resources import LocalResourceResolver
from agent_runtime_framework.tools.registry import ToolRegistry


@dataclass(slots=True)
class ApplicationContext:
    resource_repository: Any
    session_memory: Any = field(default_factory=InMemorySessionMemory)
    index_memory: Any | None = None
    policy: Any = None
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    config: dict[str, Any] = field(default_factory=dict)
    llm_client: Any = None
    llm_model: str = "gpt-5.4"
    services: dict[str, Any] = field(default_factory=dict)
    resource_resolver: Any = field(default_factory=LocalResourceResolver)
    observer: RunObserver = field(default_factory=InMemoryRunObserver)
    working_memory_factory: Callable[[], WorkingMemory] = WorkingMemory

    def __post_init__(self) -> None:
        if self.index_memory is None:
            self.index_memory = _build_default_index_memory(self.resource_repository, self.config)


def _build_default_index_memory(resource_repository: Any, config: dict[str, Any]) -> Any:
    mode = str(config.get("index_memory_mode") or "markdown").strip().lower()
    if mode == "memory":
        return InMemoryIndexMemory()
    memory_path = _default_index_memory_path(resource_repository, config)
    if memory_path is None:
        return InMemoryIndexMemory()
    return MarkdownIndexMemory(memory_path)


def _default_index_memory_path(resource_repository: Any, config: dict[str, Any]) -> Path | None:
    configured_path = str(config.get("index_memory_path") or "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    configured_root = str(config.get("default_directory") or "").strip()
    if configured_root:
        return Path(configured_root).expanduser().resolve() / ".arf" / "memory.md"
    roots = getattr(resource_repository, "allowed_roots", [])
    if not roots:
        return None
    return Path(roots[0]).expanduser().resolve() / ".arf" / "memory.md"


__all__ = ["ApplicationContext"]
