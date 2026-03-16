from __future__ import annotations

from types import SimpleNamespace

from agent_runtime_framework.applications import ApplicationContext, run_stage_parser
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(content))


def _context():
    return ApplicationContext(
        resource_repository=LocalFileResourceRepository(["/tmp"]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={},
    )


def test_run_stage_parser_prefers_custom_service():
    context = _context()
    context.services["demo_parser"] = lambda user_input, _context: {"value": user_input.upper()}

    result = run_stage_parser(
        context=context,
        service_name="demo_parser",
        service_args=("hello",),
        llm_system_prompt="return json",
        llm_user_prompt="hello",
        normalizer=lambda parsed: parsed.get("value"),
        fallback=lambda: "fallback",
    )

    assert result == "HELLO"


def test_run_stage_parser_uses_llm_when_no_custom_service():
    context = _context()
    context.llm_client = _FakeLLM('{"value":"from-llm"}')

    result = run_stage_parser(
        context=context,
        service_name="demo_parser",
        service_args=("hello",),
        llm_system_prompt="return json",
        llm_user_prompt="hello",
        normalizer=lambda parsed: parsed.get("value"),
        fallback=lambda: "fallback",
    )

    assert result == "from-llm"


def test_run_stage_parser_falls_back_when_no_parser_succeeds():
    context = _context()

    result = run_stage_parser(
        context=context,
        service_name="missing_parser",
        service_args=("hello",),
        llm_system_prompt=None,
        llm_user_prompt=None,
        normalizer=lambda parsed: parsed.get("value"),
        fallback=lambda: "fallback",
    )

    assert result == "fallback"


def test_run_stage_parser_supports_non_string_stage_payloads():
    context = _context()
    context.services["demo_parser"] = lambda payload, _context: {"mode": payload["mode"]}

    result = run_stage_parser(
        context=context,
        service_name="demo_parser",
        service_args=({"mode": "preview"},),
        llm_system_prompt="return json",
        llm_user_prompt="preview",
        normalizer=lambda parsed: parsed.get("mode"),
        fallback=lambda: "fallback",
    )

    assert result == "preview"
