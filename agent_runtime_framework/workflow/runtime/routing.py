from __future__ import annotations

from typing import Any, TypedDict


class RootGraphPayload(TypedDict):
    route: str
    intent: str


class RuntimePayload(TypedDict, total=False):
    status: str
    final_answer: str
    execution_trace: list[dict[str, Any]]
    root_graph: RootGraphPayload
