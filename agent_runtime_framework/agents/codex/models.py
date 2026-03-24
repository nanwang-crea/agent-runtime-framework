from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class VerificationResult:
    success: bool
    summary: str
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CodexEvaluationDecision:
    status: str = "abstain"
    next_action: "CodexAction | None" = None
    summary: str = ""


@dataclass(slots=True)
class CodexAction:
    kind: str
    instruction: str
    risk_class: str = "low"
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
    observation: str | None = None


@dataclass(slots=True)
class CodexActionResult:
    status: str
    final_output: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    needs_approval: bool = False
    approval_reason: str = ""
    risk_class: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CodexTask:
    goal: str
    actions: list[CodexAction]
    task_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "pending"
    summary: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    verification: VerificationResult | None = None
