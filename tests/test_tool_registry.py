from pathlib import Path

import pytest
from threading import Event, Thread
from types import SimpleNamespace

from agent_runtime_framework.tools.specs import ToolSpec
from agent_runtime_framework.tools.executor import execute_tool_call
from agent_runtime_framework.tools.models import ToolCall
from agent_runtime_framework.tools.registry import ToolRegistry


def _echo_tool(task, context, arguments):
    return {"echo": arguments["value"]}


def _failing_once_tool_factory():
    state = {"attempts": 0}

    def _tool(task, context, arguments):
        state["attempts"] += 1
        if state["attempts"] == 1:
            raise RuntimeError("fail once")
        return {"ok": True}

    return _tool


def test_registry_rejects_duplicate_tools():
    tool = ToolSpec(
        name="echo",
        description="echo input",
        executor=_echo_tool,
    )
    registry = ToolRegistry([tool])

    with pytest.raises(ValueError):
        registry.register(tool)


def test_execute_tool_call_retries_until_success():
    tool = ToolSpec(
        name="retrying",
        description="retry once",
        executor=_failing_once_tool_factory(),
        max_retries=1,
    )

    result = execute_tool_call(
        tool,
        ToolCall(tool_name="retrying", arguments={"value": "x"}),
        task=None,
        context=None,
    )

    assert result.success is True
    assert result.attempt_count == 2


def test_execute_tool_call_applies_runtime_hooks():
    events: list[tuple[str, str]] = []

    class _Runtime:
        def before_tool_call(self, tool, call, task):
            events.append(("before", tool.name))
            return ToolCall(tool_name=call.tool_name, arguments={"value": f"{call.arguments['value']}-before"})

        def after_tool_call(self, tool, call, result, task):
            events.append(("after", tool.name))
            result.output = {"echo": f"{result.output['echo']}-after"}
            return result

    tool = ToolSpec(
        name="echo",
        description="echo input",
        executor=_echo_tool,
    )
    context = SimpleNamespace(application_context=SimpleNamespace(services={"tool_runtime": _Runtime()}))

    result = execute_tool_call(
        tool,
        ToolCall(tool_name="echo", arguments={"value": "x"}),
        task=None,
        context=context,
    )

    assert result.success is True
    assert result.output == {"echo": "x-before-after"}
    assert events == [("before", "echo"), ("after", "echo")]


def test_execute_tool_call_serializes_calls_with_same_argument():
    started = Event()
    release = Event()
    order: list[str] = []

    def _tool(task, context, arguments):
        order.append(f"start:{arguments['path']}")
        if not started.is_set():
            started.set()
            assert release.wait(timeout=1.0)
        order.append(f"end:{arguments['path']}")
        return {"ok": True}

    tool = ToolSpec(
        name="writer",
        description="serialized write",
        executor=_tool,
        serialize_by_argument="path",
    )
    results: list[object] = []

    def _run_call():
        results.append(
            execute_tool_call(
                tool,
                ToolCall(tool_name="writer", arguments={"path": "same.txt"}),
                task=None,
                context=None,
            )
        )

    first = Thread(target=_run_call)
    second = Thread(target=_run_call)

    first.start()
    assert started.wait(timeout=1.0)
    second.start()
    release.set()
    first.join()
    second.join()

    assert [result.success for result in results] == [True, True]
    assert order == [
        "start:same.txt",
        "end:same.txt",
        "start:same.txt",
        "end:same.txt",
    ]


def test_tool_spec_can_load_prompt_assets(tmp_path: Path):
    asset = tmp_path / "tool.md"
    asset.write_text(
        "snippet: Read bounded excerpts.\n"
        "- Prefer excerpts for summary tasks.\n"
        "- Only read full text when the user explicitly asks for it.\n",
        encoding="utf-8",
    )

    tool = ToolSpec(
        name="excerpt",
        description="read excerpt",
        executor=_echo_tool,
        prompt_asset_path=str(asset),
    )

    assert tool.prompt_snippet == "Read bounded excerpts."
    assert tool.prompt_guidelines == [
        "Prefer excerpts for summary tasks.",
        "Only read full text when the user explicitly asks for it.",
    ]


def test_execute_tool_call_repairs_argument_aliases():
    captured: dict[str, object] = {}

    def _tool(task, context, arguments):
        captured.update(arguments)
        return {"ok": True, "path": arguments["path"]}

    tool = ToolSpec(
        name="reader",
        description="read file",
        executor=_tool,
        input_schema={"path": "string"},
        argument_aliases={"path": ("file_path", "filepath")},
    )

    result = execute_tool_call(
        tool,
        ToolCall(tool_name="reader", arguments={"file_path": "docs/readme.md"}),
        task=None,
        context=None,
    )

    assert result.success is True
    assert captured["path"] == "docs/readme.md"


def test_execute_tool_call_returns_structured_validation_error_for_type_mismatch():
    tool = ToolSpec(
        name="counter",
        description="count lines",
        executor=lambda task, context, arguments: {"ok": True},
        input_schema={"max_lines": "integer"},
    )

    result = execute_tool_call(
        tool,
        ToolCall(tool_name="counter", arguments={"max_lines": "ten"}),
        task=None,
        context=None,
    )

    assert result.success is False
    assert result.metadata["error"]["code"] == "TOOL_VALIDATION_ERROR"
    assert result.metadata["error"]["field"] == "max_lines"


def test_execute_tool_call_returns_structured_validation_error_for_missing_required_argument():
    tool = ToolSpec(
        name="reader",
        description="read file",
        executor=lambda task, context, arguments: {"ok": True},
        input_schema={"path": "string"},
        required_arguments=("path",),
    )

    result = execute_tool_call(
        tool,
        ToolCall(tool_name="reader", arguments={}),
        task=None,
        context=None,
    )

    assert result.success is False
    assert result.metadata["error"]["code"] == "TOOL_VALIDATION_ERROR"
    assert result.metadata["error"]["field"] == "path"
