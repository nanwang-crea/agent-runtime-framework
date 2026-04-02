from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_runtime_framework.demo.pending_run_registry import PendingRunRegistry
from agent_runtime_framework.demo.workflow_payload_builder import WorkflowPayloadBuilder
from agent_runtime_framework.workflow import WorkflowRun


GetClarificationFn = Callable[[], dict[str, Any] | None]
SetClarificationFn = Callable[[dict[str, Any] | None], None]


@dataclass(slots=True)
class WorkflowPresentationService:
    payload_builder: WorkflowPayloadBuilder
    pending_run_registry: PendingRunRegistry
    get_pending_clarification: GetClarificationFn
    set_pending_clarification: SetClarificationFn

    def build_payload(self, run: WorkflowRun) -> dict[str, Any]:
        resume_token_id = self.pending_run_registry.register(run)
        payload, pending_clarification = self.payload_builder.build(run, resume_token_id=resume_token_id)
        self.set_pending_clarification(pending_clarification)
        return payload
