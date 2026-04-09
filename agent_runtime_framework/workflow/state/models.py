from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    quality_signals: list[dict[str, Any]]
    reasoning_trace: list[dict[str, Any]]
    conflicts: list[str]


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
        "quality_signals": [item for item in (data.get("quality_signals", []) or []) if isinstance(item, dict)],
        "reasoning_trace": [item for item in (data.get("reasoning_trace", []) or []) if isinstance(item, dict)],
        "conflicts": [str(item) for item in (data.get("conflicts", []) or []) if str(item).strip()],
    }


RUN_STATUS_PENDING = "pending"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
RUN_STATUS_WAITING_INPUT = "waiting_input"

NODE_STATUS_PENDING = "pending"
NODE_STATUS_RUNNING = "running"
NODE_STATUS_COMPLETED = "completed"
NODE_STATUS_FAILED = "failed"
NODE_STATUS_WAITING_APPROVAL = "waiting_approval"

FILESYSTEM_WRITE_NODE_TYPES = (
    "create_path",
    "move_path",
    "delete_path",
)

TEXT_EDIT_NODE_TYPES = (
    "apply_patch",
    "write_file",
    "append_text",
)

GRAPH_NATIVE_WRITE_NODE_TYPES = FILESYSTEM_WRITE_NODE_TYPES + TEXT_EDIT_NODE_TYPES


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
class InteractionRequest:
    kind: str
    prompt: str
    summary: str = ""
    items: list[str] = field(default_factory=list)
    source_node_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConversationTurn:
    role: str
    content: str
    run_id: str | None = None


@dataclass(slots=True)
class NodeResult:
    status: str
    output: Any = None
    references: list[str] = field(default_factory=list)
    error: str | None = None
    approval_data: dict[str, Any] = field(default_factory=dict)
    interaction_request: InteractionRequest | None = None


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
    pending_interaction: InteractionRequest | None = None
    final_output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def restore_interaction_request(payload: Any) -> InteractionRequest | None:
    if isinstance(payload, InteractionRequest):
        return payload
    if not isinstance(payload, dict):
        return None
    source_node_id = payload.get("source_node_id")
    return InteractionRequest(
        kind=str(payload.get("kind") or ""),
        prompt=str(payload.get("prompt") or ""),
        summary=str(payload.get("summary") or ""),
        items=[str(item) for item in payload.get("items", []) or [] if str(item).strip()],
        source_node_id=str(source_node_id) if source_node_id is not None else None,
        metadata=dict(payload.get("metadata") or {}),
    )


def restore_node_result(payload: Any) -> NodeResult | None:
    if isinstance(payload, NodeResult):
        return payload
    if not isinstance(payload, dict):
        return None
    return NodeResult(
        status=str(payload.get("status") or NODE_STATUS_PENDING),
        output=payload.get("output"),
        references=[str(item) for item in payload.get("references", []) or [] if str(item).strip()],
        error=payload.get("error"),
        approval_data=dict(payload.get("approval_data") or {}),
        interaction_request=restore_interaction_request(payload.get("interaction_request")),
    )


@dataclass(slots=True)
class GoalSpec:
    original_goal: str
    primary_intent: str
    requires_target_interpretation: bool = False
    requires_search: bool = False
    requires_read: bool = False
    requires_verification: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubTaskSpec:
    task_id: str
    task_profile: str
    target: str | None = None
    depends_on: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GoalEnvelope:
    goal: str
    normalized_goal: str
    intent: str
    target_hints: list[str] = field(default_factory=list)
    memory_snapshot: dict[str, Any] = field(default_factory=dict)
    workspace_snapshot: dict[str, Any] = field(default_factory=dict)
    policy_context: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PlannedNode:
    node_id: str
    node_type: str
    reason: str
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    requires_approval: bool = False

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PlannedSubgraph:
    iteration: int
    planner_summary: str
    nodes: list[PlannedNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "planner_summary": self.planner_summary,
            "nodes": [node.as_payload() for node in self.nodes],
            "edges": [asdict(edge) for edge in self.edges],
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class JudgeDecision:
    status: str
    reason: str
    missing_evidence: list[str] = field(default_factory=list)
    coverage_report: dict[str, Any] = field(default_factory=dict)
    replan_hint: dict[str, Any] = field(default_factory=dict)
    diagnosis: dict[str, Any] = field(default_factory=dict)
    strategy_guidance: dict[str, Any] = field(default_factory=dict)
    allowed_next_node_types: list[str] = field(default_factory=list)
    blocked_next_node_types: list[str] = field(default_factory=list)
    must_cover: list[str] = field(default_factory=list)
    planner_instructions: str = ""

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SessionMemoryState:
    last_active_target: str | None = None
    recent_paths: list[str] = field(default_factory=list)
    last_action_summary: str | None = None
    last_read_files: list[str] = field(default_factory=list)
    last_clarification: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "last_active_target": self.last_active_target,
            "recent_paths": list(self.recent_paths),
            "last_action_summary": self.last_action_summary,
            "last_read_files": list(self.last_read_files),
            "last_clarification": dict(self.last_clarification) if isinstance(self.last_clarification, dict) else None,
        }


@dataclass(slots=True)
class WorkingMemory:
    active_target: str | None = None
    confirmed_targets: list[str] = field(default_factory=list)
    excluded_targets: list[str] = field(default_factory=list)
    current_step: str | None = None
    open_issues: list[str] = field(default_factory=list)
    last_tool_result_summary: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "active_target": self.active_target,
            "confirmed_targets": list(self.confirmed_targets),
            "excluded_targets": list(self.excluded_targets),
            "current_step": self.current_step,
            "open_issues": list(self.open_issues),
            "last_tool_result_summary": dict(self.last_tool_result_summary) if isinstance(self.last_tool_result_summary, dict) else None,
        }


def _goal_session_memory(goal_envelope: GoalEnvelope) -> SessionMemoryState:
    snapshot = dict(getattr(goal_envelope, "memory_snapshot", None) or {})
    recent_paths = [str(item) for item in snapshot.get("focused_resources", []) or [] if str(item).strip()]
    return SessionMemoryState(
        last_active_target=recent_paths[0] if recent_paths else None,
        recent_paths=recent_paths,
        last_action_summary=str(snapshot.get("last_summary") or "").strip() or None,
        last_read_files=recent_paths,
        last_clarification=None,
    )


@dataclass(slots=True)
class WorkflowMemoryState:
    session_memory: SessionMemoryState = field(default_factory=SessionMemoryState)
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    long_term_memory: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "session_memory": self.session_memory.as_payload(),
            "working_memory": self.working_memory.as_payload(),
            "long_term_memory": dict(self.long_term_memory),
        }


@dataclass(slots=True)
class AgentGraphState:
    run_id: str
    goal_envelope: GoalEnvelope
    current_iteration: int = 0
    aggregated_payload: AggregatedWorkflowPayload = field(default_factory=normalize_aggregated_workflow_payload)
    planned_subgraphs: list[PlannedSubgraph] = field(default_factory=list)
    judge_history: list[JudgeDecision] = field(default_factory=list)
    appended_node_ids: list[str] = field(default_factory=list)
    iteration_summaries: list[dict[str, Any]] = field(default_factory=list)
    failure_history: list[dict[str, Any]] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)
    attempted_strategies: list[str] = field(default_factory=list)
    recovery_history: list[dict[str, Any]] = field(default_factory=list)
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    memory_state: WorkflowMemoryState = field(default_factory=WorkflowMemoryState)

    def as_payload(self) -> dict[str, Any]:
        return serialize_agent_graph_state(self)


def new_agent_graph_state(*, run_id: str, goal_envelope: GoalEnvelope) -> AgentGraphState:
    return AgentGraphState(
        run_id=run_id,
        goal_envelope=goal_envelope,
        memory_state=WorkflowMemoryState(session_memory=_goal_session_memory(goal_envelope)),
    )


def build_agent_graph_execution_summary(state: AgentGraphState) -> dict[str, Any]:
    payload = normalize_aggregated_workflow_payload(state.aggregated_payload)
    latest_decision = state.judge_history[-1] if state.judge_history else None
    latest_failure = dict(state.failure_history[-1]) if state.failure_history else None
    latest_recovery = dict(state.recovery_history[-1]) if state.recovery_history else None
    latest_repair = dict(state.repair_history[-1]) if state.repair_history else None
    execution_failed = str((latest_recovery or {}).get("trigger") or "") == "execution_failed"

    if latest_decision is not None:
        last_judge_status = str(latest_decision.status or "")
        last_judge_reason = str(latest_decision.reason or "")
        latest_diagnosis = dict(latest_decision.diagnosis)
        latest_strategy_guidance = dict(latest_decision.strategy_guidance)
        missing_evidence = list(latest_decision.missing_evidence)
    elif execution_failed:
        last_judge_status = "execution_failed"
        last_judge_reason = str((latest_recovery or {}).get("reason") or "workflow execution failed")
        latest_diagnosis = {}
        latest_strategy_guidance = {}
        missing_evidence = list(state.open_issues)
    else:
        last_judge_status = ""
        last_judge_reason = ""
        latest_diagnosis = {}
        latest_strategy_guidance = {}
        missing_evidence = list(state.open_issues)

    return {
        "current_iteration": state.current_iteration,
        "last_judge_status": last_judge_status,
        "last_judge_reason": last_judge_reason,
        "missing_evidence": missing_evidence,
        "appended_node_ids": list(state.appended_node_ids),
        "summaries": list(payload.get("summaries", []) or []),
        "verification": dict(payload.get("verification") or {}) if isinstance(payload.get("verification"), dict) else None,
        "quality_signals": [dict(item) for item in payload.get("quality_signals", []) or [] if isinstance(item, dict)],
        "conflicts": [str(item) for item in payload.get("conflicts", []) or [] if str(item).strip()],
        "open_issues": list(state.open_issues),
        "attempted_strategies": list(state.attempted_strategies),
        "latest_diagnosis": latest_diagnosis,
        "latest_strategy_guidance": latest_strategy_guidance,
        "latest_failure": latest_failure,
        "latest_recovery_decision": latest_recovery,
        "repair_count": len(state.repair_history),
        "latest_repair": latest_repair,
    }


def serialize_agent_graph_state(state: AgentGraphState) -> dict[str, Any]:
    return {
        "run_id": state.run_id,
        "goal_envelope": state.goal_envelope.as_payload(),
        "current_iteration": state.current_iteration,
        "aggregated_payload": normalize_aggregated_workflow_payload(state.aggregated_payload),
        "execution_summary": build_agent_graph_execution_summary(state),
        "planned_subgraphs": [subgraph.as_payload() for subgraph in state.planned_subgraphs],
        "judge_history": [decision.as_payload() for decision in state.judge_history],
        "appended_node_ids": list(state.appended_node_ids),
        "iteration_summaries": [dict(item) for item in state.iteration_summaries],
        "failure_history": [dict(item) for item in state.failure_history],
        "open_issues": list(state.open_issues),
        "attempted_strategies": list(state.attempted_strategies),
        "recovery_history": [dict(item) for item in state.recovery_history],
        "repair_history": [dict(item) for item in state.repair_history],
        "memory_state": state.memory_state.as_payload(),
    }
