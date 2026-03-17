from __future__ import annotations

from agent_runtime_framework.assistant import MCPClientAdapter


class _FakeMCPClient:
    def list_tools(self):
        return [
            {
                "name": "external_search",
                "description": "Search external sources",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                "safety_level": "network",
            }
        ]

    def call_tool(self, name: str, arguments: dict):
        return f"tool:{name}:{arguments.get('query', '')}"


def test_mcp_client_adapter_exposes_discoverable_tools():
    provider = MCPClientAdapter(_FakeMCPClient())

    tools = provider.list_tools()

    assert len(tools) == 1
    assert tools[0].name == "external_search"
    assert tools[0].safety_level == "network"


def test_mcp_client_adapter_runner_calls_client_tool():
    provider = MCPClientAdapter(_FakeMCPClient())

    tool = provider.list_tools()[0]
    result = tool.runner("search cats", None, None)

    assert result == "tool:external_search:search cats"
