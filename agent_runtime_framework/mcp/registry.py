from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime_framework.mcp.models import McpCapabilityRef, McpServiceRef


@dataclass(slots=True)
class McpRegistry:
    _services: dict[str, McpServiceRef] = field(default_factory=dict)
    _capabilities: dict[tuple[str, str], McpCapabilityRef] = field(default_factory=dict)

    def register_service(self, service: McpServiceRef) -> None:
        self._services[service.server_id] = service

    def register_capability(self, capability: McpCapabilityRef) -> None:
        self._capabilities[(capability.server_id, capability.capability_id)] = capability

    def get_service(self, server_id: str) -> McpServiceRef | None:
        return self._services.get(str(server_id).strip())

    def list_services(self) -> list[McpServiceRef]:
        return [self._services[key] for key in sorted(self._services)]

    def get_capability(self, server_id: str, capability_id: str) -> McpCapabilityRef | None:
        return self._capabilities.get((str(server_id).strip(), str(capability_id).strip()))

    def list_capabilities(self, server_id: str | None = None) -> list[McpCapabilityRef]:
        if server_id is None:
            return [self._capabilities[key] for key in sorted(self._capabilities)]
        normalized = str(server_id).strip()
        return [
            capability
            for (service_id, _), capability in sorted(self._capabilities.items())
            if service_id == normalized
        ]
