from __future__ import annotations

from types import SimpleNamespace

import agent_runtime_framework as arf
from agent_runtime_framework.mcp import McpCapabilityRef, McpRegistry, McpServiceRef
from agent_runtime_framework.skills import McpSkillProvider, SkillResult, SkillRuntime, ToolSkillProvider
from agent_runtime_framework.tools import ToolRegistry, ToolSpec


class _RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, object]] = []

    def supports(self, skill_name: str) -> bool:
        return skill_name == "demo-skill"

    def invoke(self, skill_name: str, input: dict[str, object], context: object) -> SkillResult:
        self.calls.append((skill_name, input, context))
        return SkillResult(
            name=skill_name,
            success=True,
            summary="demo complete",
            payload={"ok": True},
            memory_hint={"scope": "project_conventions", "values": {"style": "compact"}},
        )


def test_public_surface_exports_skill_runtime_types():
    assert hasattr(arf, "SkillRuntime")
    assert hasattr(arf, "SkillResult")
    assert hasattr(arf, "ToolSkillProvider")
    assert hasattr(arf, "McpSkillProvider")


def test_skill_runtime_dispatches_to_supporting_provider():
    provider = _RecordingProvider()
    runtime = SkillRuntime([provider])

    result = runtime.invoke("demo-skill", {"value": 1}, context={"workspace": "."})

    assert result.summary == "demo complete"
    assert provider.calls == [("demo-skill", {"value": 1}, {"workspace": "."})]


def test_tool_skill_provider_wraps_tool_registry_execution():
    registry = ToolRegistry(
        [
            ToolSpec(
                name="read_demo",
                description="read demo",
                executor=lambda task, context, arguments: {
                    "summary": f"read {arguments['path']}",
                    "path": arguments["path"],
                    "changed_paths": [],
                    "memory_hint": {"scope": "path_aliases", "values": {"demo": arguments["path"]}},
                },
                required_arguments=("path",),
            )
        ]
    )
    provider = ToolSkillProvider(registry)

    result = provider.invoke("read_demo", {"arguments": {"path": "README.md"}}, context=SimpleNamespace())

    assert result.success is True
    assert result.summary == "read README.md"
    assert result.references == ["README.md"]
    assert result.memory_hint == {"scope": "path_aliases", "values": {"demo": "README.md"}}


def test_mcp_skill_provider_uses_registry_and_normalizes_output():
    registry = McpRegistry()
    registry.register_service(McpServiceRef(server_id="docs", label="Docs"))
    registry.register_capability(McpCapabilityRef(server_id="docs", capability_id="search"))
    captured: list[tuple[str, dict, object]] = []
    provider = McpSkillProvider(
        registry=registry,
        invoker=lambda skill_name, payload, context: (
            captured.append((skill_name, payload, context))
            or {
                "success": True,
                "summary": "mcp search complete",
                "references": ["docs://result/1"],
            }
        ),
    )

    result = provider.invoke("mcp:docs/search", {"query": "runtime"}, context={"workspace": "."})

    assert provider.supports("mcp:docs/search") is True
    assert result.summary == "mcp search complete"
    assert result.references == ["docs://result/1"]
    assert captured == [("mcp:docs/search", {"query": "runtime"}, {"workspace": "."})]


def test_mcp_registry_lists_capabilities_by_service():
    registry = McpRegistry()
    registry.register_service(McpServiceRef(server_id="docs", label="Docs"))
    registry.register_capability(McpCapabilityRef(server_id="docs", capability_id="search"))
    registry.register_capability(McpCapabilityRef(server_id="docs", capability_id="fetch"))

    assert registry.get_capability("docs", "search") is not None
    assert [cap.capability_id for cap in registry.list_capabilities("docs")] == ["fetch", "search"]
