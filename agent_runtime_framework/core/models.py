from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class Task(BaseModel):
    user_input: str
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class RuntimeContext:
    services: dict[str, Any] = field(default_factory=dict)
    config: Any = None
    llm_client: Any = None


class Observation(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class StepRecord(BaseModel):
    name: str
    status: str
    detail: str | None = None


class RuntimeLimits(BaseModel):
    max_steps: int = 12
    max_tool_calls: int = 8
    max_runtime_seconds: float = 30.0


class RunResult(BaseModel):
    status: str
    final_answer: str = ""
    steps: list[StepRecord] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    termination_reason: str | None = None
