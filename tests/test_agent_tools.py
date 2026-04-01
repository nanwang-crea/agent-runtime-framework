from agent_runtime_framework.agent_tools import AgentToolCall, AgentToolRegistry, AgentToolSpec, execute_agent_tool
from agent_runtime_framework.agents import AgentRegistry, builtin_agent_definitions


def test_agent_tool_executor_normalizes_runtime_result():
    registry = AgentRegistry()
    registry.register_many(builtin_agent_definitions())

    call = AgentToolCall(agent_id="explore", message="inspect repo", enabled_skills=["repository_overview"])

    result = execute_agent_tool(
        call,
        registry,
        lambda _call, definition, prompt: {
            "status": "completed",
            "output": prompt,
            "execution_backend": definition.executor_kind,
            "metadata": {"agent": definition.agent_id},
        },
    )

    assert result.status == "completed"
    assert result.agent_id == "explore"
    assert result.execution_backend == "workflow"
    assert "repository_overview" in result.output


def test_agent_tool_registry_lists_specs():
    registry = AgentToolRegistry()
    registry.register(AgentToolSpec(name="explore_tool", default_agent_id="explore"))

    assert registry.get("explore_tool").default_agent_id == "explore"
    assert [item.name for item in registry.list()] == ["explore_tool"]
