from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from agent_runtime_framework.resources.models import ResourceRef
from agent_runtime_framework.resources.repository import ResourceRepository


@dataclass(slots=True)
class ResolveRequest:
    user_input: str
    default_directory: ResourceRef
    last_focused: list[ResourceRef] = field(default_factory=list)


class ResourceResolver(Protocol):
    def resolve(self, request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]: ...


ResolverStrategy = Callable[[ResolveRequest, ResourceRepository], list[ResourceRef]]


class ResolverPipeline:
    def __init__(self, strategies: list[ResolverStrategy]) -> None:
        self._strategies = list(strategies)

    def resolve(self, request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
        for strategy in self._strategies:
            resolved = strategy(request, repository)
            if resolved:
                return resolved
        return []

    @classmethod
    def default(cls) -> "ResolverPipeline":
        return cls([resolve_last_focus, resolve_default_directory])


def resolve_last_focus(request: ResolveRequest, _repository: ResourceRepository) -> list[ResourceRef]:
    text = request.user_input.strip()
    if "刚才" in text or "上一个" in text or "那个文件" in text:
        return list(request.last_focused)
    return []


def resolve_default_directory(request: ResolveRequest, _repository: ResourceRepository) -> list[ResourceRef]:
    text = request.user_input.strip()
    if "当前目录" in text or "这个目录" in text:
        return [request.default_directory]
    return []


class LocalResourceResolver:
    def __init__(self, pipeline: ResolverPipeline | None = None) -> None:
        self.pipeline = pipeline or ResolverPipeline.default()

    def resolve(self, request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
        return self.pipeline.resolve(request, repository)
