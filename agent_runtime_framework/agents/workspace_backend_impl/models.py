from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class VerificationResult:
    success: bool
    summary: str
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TaskIntent:
    task_kind: str = "chat"
    user_intent: str = "general_chat"
    goal_mode: str = "direct_answer"
    scope_kind: str = "unknown"
    target_ref: str = ""
    target_hint: str = ""
    target_type: str = "unknown"
    target_confidence: float = 0.0
    confidence: float = 0.0
    needs_clarification: bool = False
    needs_grounding: bool = False
    expected_output: str = "direct_answer"
    allowed_strategy_family: list[str] = field(default_factory=list)
    suggested_tool_chain: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceItem:
    source: str
    kind: str
    summary: str = ""
    path: str = ""
    content: str = ""
    relevance: float = 0.0


@dataclass(slots=True)
class ConfidenceState:
    intent_confidence: float = 0.0
    target_confidence: float = 0.0
    evidence_confidence: float = 0.0
    answer_confidence: float = 0.0


@dataclass(slots=True)
class TaskState:
    task_intent: TaskIntent = field(default_factory=TaskIntent)
    resolved_target: str = ""
    resource_semantics: dict[str, Any] = field(default_factory=dict)
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    known_facts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    read_paths: list[str] = field(default_factory=list)
    modified_paths: list[str] = field(default_factory=list)
    pending_verifications: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    typed_claims: list[dict[str, str]] = field(default_factory=list)
    pending_actions: list[str] = field(default_factory=list)
    plan_state: dict[str, Any] = field(default_factory=dict)
    confidence_state: ConfidenceState = field(default_factory=ConfidenceState)
    answer_mode: str = "direct_answer"


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
    runtime_persona: str = "general"
    intent: TaskIntent = field(default_factory=TaskIntent)
    state: TaskState = field(default_factory=TaskState)
    task_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "pending"
    summary: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    verification: VerificationResult | None = None
    memory: TaskState | None = None
    plan: CodexPlan | None = None

    def __post_init__(self) -> None:
        if self.memory is None:
            self.memory = self.state
