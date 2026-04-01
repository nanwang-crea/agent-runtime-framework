from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ClarificationExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        prompt = str(node.metadata.get("prompt") or node.metadata.get("instruction") or node.metadata.get("summary") or "Please clarify the request.")
        payload = {
            "prompt": prompt,
            "summary": prompt,
            "clarification_required": True,
        }
        run.shared_state["clarification_request"] = payload
        return NodeResult(status=NODE_STATUS_COMPLETED, output=payload)
