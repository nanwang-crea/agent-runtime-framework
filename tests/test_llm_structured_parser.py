from __future__ import annotations

from types import SimpleNamespace

from agent_runtime_framework.runtime import parse_structured_output


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._content),
                )
            ]
        )


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(content))


def test_parse_structured_output_returns_normalized_payload():
    client = _FakeLLM('{"action":"summarize","target_name":"note.md"}')

    result = parse_structured_output(
        client,
        model="demo-model",
        system_prompt="return json",
        user_prompt="summarize note.md",
        normalizer=lambda data: {"action": data["action"], "target_name": data.get("target_name")},
    )

    assert result == {"action": "summarize", "target_name": "note.md"}


def test_parse_structured_output_returns_none_on_invalid_json():
    client = _FakeLLM("not-json")

    result = parse_structured_output(
        client,
        model="demo-model",
        system_prompt="return json",
        user_prompt="summarize note.md",
        normalizer=lambda data: data,
    )

    assert result is None
