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
    ResolveRequest,
    ResolverPipeline,
    ResourceResolver,
    resolve_default_directory,
    resolve_last_focus,
)

__all__ = [
    "DirectoryResource",
    "DocumentChunkResource",
    "FileResource",
    "InMemoryResourceIndex",
    "LocalFileResourceRepository",
    "LocalResourceResolver",
    "ResolveRequest",
    "ResolverPipeline",
    "Resource",
    "ResourceIndex",
    "ResourceKind",
    "ResourceRef",
    "ResourceRepository",
    "ResourceResolver",
    "resolve_default_directory",
    "resolve_last_focus",
]
