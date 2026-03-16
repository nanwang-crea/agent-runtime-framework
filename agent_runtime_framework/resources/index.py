from __future__ import annotations

from typing import Protocol

from agent_runtime_framework.resources.models import Resource


class ResourceIndex(Protocol):
    def get(self, resource_id: str) -> Resource | None: ...

    def put(self, resource: Resource) -> None: ...


class InMemoryResourceIndex:
    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}

    def get(self, resource_id: str) -> Resource | None:
        return self._resources.get(resource_id)

    def put(self, resource: Resource) -> None:
        self._resources[resource.ref.resource_id] = resource
