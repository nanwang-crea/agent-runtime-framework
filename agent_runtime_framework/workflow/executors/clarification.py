from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.workflow.runtime.protocols import RuntimeContextLike

from agent_runtime_framework.workflow.state.models import InteractionRequest, NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ClarificationExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        prompt = str(node.metadata.get("prompt") or node.metadata.get("instruction") or node.metadata.get("summary") or "Please clarify the request.")
        payload = {
            "prompt": prompt,
            "summary": prompt,
            "clarification_required": True,
        }
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output=payload,
            interaction_request=InteractionRequest(
                kind="clarification",
                prompt=prompt,
                summary=prompt,
                source_node_id=node.node_id,
            ),
        )
