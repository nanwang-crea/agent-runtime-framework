from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from agent_runtime_framework.resources.models import ResourceKind, ResourceRef
from agent_runtime_framework.resources.repository import ResourceRepository


@dataclass(slots=True)
class ResolveRequest:
    user_input: str
    default_directory: ResourceRef
    target_hint: str = ""
    last_focused: list[ResourceRef] = field(default_factory=list)
    memory_hints: list["ResolveHint"] = field(default_factory=list)


class ResourceResolver(Protocol):
    def resolve(self, request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]: ...


ResolverStrategy = Callable[[ResolveRequest, ResourceRepository], list[ResourceRef]]


@dataclass(slots=True)
class ResolvedResourceSemantics:
    ref: ResourceRef
    resource_kind: str
    is_container: bool
    allowed_actions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResolveHint:
    path: str
    source: str = "memory"
    summary: str = ""


@dataclass(slots=True)
class ResolveState:
    status: str
    selected: ResolvedResourceSemantics | None = None
    candidates: list[ResolvedResourceSemantics] = field(default_factory=list)
    source: str = ""
    reason: str = ""


class ResolverPipeline:
    def __init__(self, strategies: list[ResolverStrategy]) -> None:
        self._strategies = list(strategies)

    def resolve(self, request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
        for strategy in self._strategies:
            resolved = strategy(request, repository)
            if resolved:
                return resolved
        return []

    def resolve_with_semantics(
        self,
        request: ResolveRequest,
        repository: ResourceRepository,
    ) -> list[ResolvedResourceSemantics]:
        resolved = self.resolve(request, repository)
        return [describe_resource_semantics(ref, repository) for ref in resolved]

    def resolve_state(self, request: ResolveRequest, repository: ResourceRepository) -> ResolveState:
        explicit = resolve_explicit_path(request, repository)
        if explicit:
            return _resolve_state_from_refs(explicit, repository, source="explicit_path", reason="matched explicit path")
        focused = resolve_last_focus(request, repository)
        if focused:
            return _resolve_state_from_refs(focused, repository, source="last_focus", reason="matched session focus")
        default_directory = resolve_default_directory(request, repository)
        if default_directory:
            return _resolve_state_from_refs(
                default_directory,
                repository,
                source="default_directory",
                reason="matched current directory reference",
            )
        hinted = resolve_memory_hint(request, repository)
        if hinted:
            return _resolve_state_from_refs(hinted, repository, source="memory_hint", reason="matched remembered target")
        named = resolve_target_name_matches(request, repository)
        if named:
            reason = "matched target name" if len(named) == 1 else "target name is ambiguous"
            return _resolve_state_from_refs(named, repository, source="target_name", reason=reason)
        return ResolveState(status="unresolved", source="none", reason="no matching resource found")

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
    candidates = _extract_path_candidates(request.user_input, request.target_hint)
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
    matches = resolve_target_name_matches(request, repository)
    return matches[:1]


def resolve_target_name_matches(request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
    candidates = _extract_path_candidates(request.user_input, request.target_hint)
    if not candidates:
        return []
    for candidate in candidates:
        matches = repository.find_by_name(request.default_directory, candidate)
        if matches:
            return matches
    return []


def resolve_memory_hint(request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
    refs: list[ResourceRef] = []
    seen: set[str] = set()
    for hint in request.memory_hints:
        path = str(hint.path or "").strip()
        if not path:
            continue
        try:
            ref = ResourceRef.for_path(_resolve_hint_path(path, request.default_directory))
            repository.get(ref)
        except (FileNotFoundError, ValueError):
            continue
        if ref.resource_id in seen:
            continue
        seen.add(ref.resource_id)
        refs.append(ref)
    return refs


def _extract_path_candidates(text: str, target_hint: str = "") -> list[str]:
    cleaned = " ".join(part for part in (text.strip(), target_hint.strip()) if part).strip()
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
    explicit_hint = target_hint.strip(" \"'[]()")
    if explicit_hint and explicit_hint not in candidates:
        candidates.append(explicit_hint)
    return candidates


def _resolve_hint_path(path: str, default_directory: ResourceRef) -> Path:
    default_path = Path(default_directory.location).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = default_path / candidate
    return candidate.resolve(strict=False)


def _resolve_state_from_refs(
    refs: list[ResourceRef],
    repository: ResourceRepository,
    *,
    source: str,
    reason: str,
) -> ResolveState:
    semantics = [describe_resource_semantics(ref, repository) for ref in refs]
    if not semantics:
        return ResolveState(status="unresolved", source=source, reason=reason)
    if len(semantics) == 1:
        return ResolveState(status="resolved", selected=semantics[0], candidates=semantics, source=source, reason=reason)
    return ResolveState(status="ambiguous", candidates=semantics, source=source, reason=reason)


def describe_resource_semantics(ref: ResourceRef, repository: ResourceRepository) -> ResolvedResourceSemantics:
    resource = repository.get(ref)
    resource_kind = str(resource.ref.kind or ref.kind)
    is_container = resource_kind == ResourceKind.DIRECTORY.value
    allowed_actions = ["list", "inspect"] if is_container else ["read", "summarize", "inspect"]
    return ResolvedResourceSemantics(
        ref=resource.ref,
        resource_kind=resource_kind,
        is_container=is_container,
        allowed_actions=allowed_actions,
    )


class LocalResourceResolver:
    def __init__(self, pipeline: ResolverPipeline | None = None) -> None:
        self.pipeline = pipeline or ResolverPipeline.default()

    def resolve(self, request: ResolveRequest, repository: ResourceRepository) -> list[ResourceRef]:
        return self.pipeline.resolve(request, repository)

    def resolve_with_semantics(
        self,
        request: ResolveRequest,
        repository: ResourceRepository,
    ) -> list[ResolvedResourceSemantics]:
        return self.pipeline.resolve_with_semantics(request, repository)

    def resolve_state(self, request: ResolveRequest, repository: ResourceRepository) -> ResolveState:
        return self.pipeline.resolve_state(request, repository)
