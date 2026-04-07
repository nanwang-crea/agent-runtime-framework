from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkspaceContext:
    application_context: Any
    services: dict[str, Any] = field(default_factory=dict)
    session: Any | None = None


@dataclass(slots=True)
class EvidenceItem:
    source: str
    kind: str
    summary: str = ""
    path: str = ""
    content: str = ""
    relevance: float = 0.0


@dataclass(slots=True)
class TaskState:
    resolved_target: str = ""
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    read_paths: list[str] = field(default_factory=list)
    modified_paths: list[str] = field(default_factory=list)
    resource_semantics: dict[str, Any] = field(default_factory=dict)
    open_questions: list[str] = field(default_factory=list)
    pending_verifications: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    typed_claims: list[dict[str, str]] = field(default_factory=list)
    pending_actions: list[str] = field(default_factory=list)
    plan_state: dict[str, Any] = field(default_factory=dict)
