from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
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
        return cls(
            [
                resolve_last_focus,
                resolve_explicit_path,
                resolve_default_directory,
                resolve_target_name,
            ]
        )


def resolve_last_focus(request: ResolveRequest, _repository: ResourceRepository) -> list[ResourceRef]:
    text = request.user_input.strip()
    if "刚才" in text or "上一个" in text or "那个文件" in text or "下面" in text or "里面" in text:
        return list(request.last_focused)
    return []


def resolve_default_directory(request: ResolveRequest, _repository: ResourceRepository) -> list[ResourceRef]:
    text = request.user_input.strip()
    if "当前目录" in text or "这个目录" in text:
        return [request.default_directory]
    return []


def resolve_explicit_path(request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
    candidates = _extract_path_candidates(request.user_input)
    if not candidates:
        return []
    default_directory = Path(request.default_directory.location)
    for candidate in candidates:
        candidate_path = Path(candidate).expanduser()
        possible_paths = [candidate_path]
        if not candidate_path.is_absolute():
            possible_paths.insert(0, default_directory / candidate_path)
        for possible_path in possible_paths:
            try:
                resource = repository.get(ResourceRef.for_path(possible_path))
            except (FileNotFoundError, ValueError):
                continue
            return [resource.ref]
    return []


def resolve_target_name(request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
    candidates = _extract_path_candidates(request.user_input)
    if not candidates:
        return []
    for candidate in candidates:
        matches = repository.find_by_name(request.default_directory, candidate)
        if matches:
            return [matches[0]]
    return []


def _extract_path_candidates(text: str) -> list[str]:
    cleaned = text.strip()
    for marker in ("读取", "读一下", "看", "打开", "总结", "总结一下", "列出", "分析"):
        cleaned = cleaned.replace(marker, " ")
    cleaned = cleaned.replace("：", " ").replace(":", " ").strip("。 ")
    raw_tokens = [token.strip(" \"'[]()") for token in cleaned.split() if token.strip(" \"'[]()")]
    candidates: list[str] = []
    for token in raw_tokens:
        if "/" in token or "." in token or token.startswith("~"):
            candidates.append(token)
            continue
        if len(raw_tokens) == 1:
            candidates.append(token)
    return candidates


class LocalResourceResolver:
    def __init__(self, pipeline: ResolverPipeline | None = None) -> None:
        self.pipeline = pipeline or ResolverPipeline.default()

    def resolve(self, request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
        return self.pipeline.resolve(request, repository)
