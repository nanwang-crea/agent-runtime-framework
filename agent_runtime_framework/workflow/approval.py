from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass(slots=True)
class WorkflowResumeToken:
    token_id: str
    node_id: str


def create_resume_token(node_id: str) -> WorkflowResumeToken:
    return WorkflowResumeToken(token_id=str(uuid4()), node_id=node_id)
