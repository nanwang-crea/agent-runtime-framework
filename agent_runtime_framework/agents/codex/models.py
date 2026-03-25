from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_runtime_framework.agents.codex.memory import CodexTaskMemory


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
    subgoal: str = "execute_step"
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
class CodexPlanTask:
    title: str
    kind: str
    task_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)
    action_indexes: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TargetSemantics:
    path: str = ""
    resource_kind: str = ""
    is_container: bool = False
    allowed_actions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CodexPlan:
    tasks: list[CodexPlanTask]
    plan_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
    target_semantics: TargetSemantics | None = None


@dataclass(slots=True)
class CodexTask:
    goal: str
    actions: list[CodexAction]
    task_profile: str = "chat"
    task_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "pending"
    summary: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    verification: VerificationResult | None = None
    memory: CodexTaskMemory = field(default_factory=CodexTaskMemory)
    plan: CodexPlan | None = None
