from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


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
