from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict
from uuid import uuid4


class EvidenceItemPayload(TypedDict, total=False):
    kind: str
    path: str
    relative_path: str
    summary: str
    score: float
    line: int
    line_start: int
    line_end: int
    start_line: int
    end_line: int
    matched_terms: list[str]
    text: str
    source: str
    content: str
    metadata: dict[str, Any]


class FactPayload(TypedDict, total=False):
    kind: str
    path: str
    summary: str
    value: Any
    metadata: dict[str, Any]


class VerificationPayload(TypedDict, total=False):
    status: str
    success: bool
    summary: str
    verification_type: NotRequired[str]
    details: NotRequired[dict[str, Any]]


class AggregatedWorkflowPayload(TypedDict):
    summaries: list[str]
    facts: list[FactPayload]
    evidence_items: list[EvidenceItemPayload]
    chunks: list[dict[str, Any]]
    artifacts: dict[str, list[Any]]
    open_questions: list[str]
    verification: VerificationPayload | None
    verification_events: list[VerificationPayload]


def normalize_aggregated_workflow_payload(output: dict[str, Any] | None = None) -> AggregatedWorkflowPayload:
    data = dict(output or {})
    summaries = [str(item) for item in data.get("summaries", []) or [] if str(item).strip()]
    single_summary = str(data.get("summary") or "").strip()
    if single_summary and single_summary not in summaries:
        summaries.append(single_summary)

    verification = data.get("verification")
    verification_events = [
        event
        for event in (data.get("verification_events", []) or [])
        if isinstance(event, dict)
    ]
    if isinstance(verification, dict) and verification not in verification_events:
        verification_events.append(verification)
    if verification is None and verification_events:
        verification = verification_events[-1]

    artifacts: dict[str, list[Any]] = {}
    for key, value in dict(data.get("artifacts") or {}).items():
        values = value if isinstance(value, list) else [value]
        artifacts[str(key)] = list(values)

    return {
        "summaries": summaries,
        "facts": [item for item in (data.get("facts", []) or []) if isinstance(item, dict)],
        "evidence_items": [item for item in (data.get("evidence_items", []) or []) if isinstance(item, dict)],
        "chunks": [item for item in (data.get("chunks", []) or []) if isinstance(item, dict)],
        "artifacts": artifacts,
        "open_questions": [str(item) for item in (data.get("open_questions", []) or []) if str(item).strip()],
        "verification": verification if isinstance(verification, dict) else None,
        "verification_events": verification_events,
    }


RUN_STATUS_PENDING = "pending"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_WAITING_APPROVAL = "waiting_approval"

NODE_STATUS_PENDING = "pending"
NODE_STATUS_RUNNING = "running"
NODE_STATUS_COMPLETED = "completed"
NODE_STATUS_FAILED = "failed"
NODE_STATUS_WAITING_APPROVAL = "waiting_approval"


@dataclass(slots=True)
class WorkflowEdge:
    source: str
    target: str
    condition: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowNode:
    node_id: str
    node_type: str
    dependencies: list[str] = field(default_factory=list)
    task_profile: str | None = None
    status: str = NODE_STATUS_PENDING
    requires_approval: bool = False
    retry_limit: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NodeResult:
    status: str
    output: Any = None
    references: list[str] = field(default_factory=list)
    error: str | None = None
    approval_data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NodeState:
    node_id: str
    status: str = NODE_STATUS_PENDING
    result: NodeResult | None = None
    error: str | None = None
    approval_requested: bool = False
    approval_granted: bool | None = None
    attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowGraph:
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowRun:
    goal: str
    run_id: str = field(default_factory=lambda: str(uuid4()))
    graph: WorkflowGraph = field(default_factory=WorkflowGraph)
    node_states: dict[str, NodeState] = field(default_factory=dict)
    shared_state: dict[str, Any] = field(default_factory=dict)
    status: str = RUN_STATUS_PENDING
    final_output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GoalSpec:
    original_goal: str
    primary_intent: str
    requires_repository_overview: bool = False
    requires_file_read: bool = False
    requires_final_synthesis: bool = False
    target_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubTaskSpec:
    task_id: str
    task_profile: str
    target: str | None = None
    depends_on: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
