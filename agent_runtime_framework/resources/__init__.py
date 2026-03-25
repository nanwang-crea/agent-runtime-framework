"""Resource models and local desktop resource providers."""

from agent_runtime_framework.resources.index import InMemoryResourceIndex, ResourceIndex
from agent_runtime_framework.resources.models import (
    DirectoryResource,
    DocumentChunkResource,
    FileResource,
    Resource,
    ResourceKind,
    ResourceRef,
)
from agent_runtime_framework.resources.repository import LocalFileResourceRepository, ResourceRepository
from agent_runtime_framework.resources.resolver import (
    LocalResourceResolver,
    ResolveHint,
    ResolveRequest,
    ResolveState,
    ResolvedResourceSemantics,
    ResolverPipeline,
    ResourceResolver,
    describe_resource_semantics,
    resolve_default_directory,
    resolve_last_focus,
    resolve_memory_hint,
)

__all__ = [
    "DirectoryResource",
    "DocumentChunkResource",
    "FileResource",
    "InMemoryResourceIndex",
    "LocalFileResourceRepository",
    "LocalResourceResolver",
    "ResolveHint",
    "ResolveRequest",
    "ResolveState",
    "ResolvedResourceSemantics",
    "ResolverPipeline",
    "Resource",
    "ResourceIndex",
    "ResourceKind",
    "ResourceRef",
    "ResourceRepository",
    "ResourceResolver",
    "describe_resource_semantics",
    "resolve_default_directory",
    "resolve_last_focus",
    "resolve_memory_hint",
]
