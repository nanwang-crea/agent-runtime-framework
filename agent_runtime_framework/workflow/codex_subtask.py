from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class CodexSubtaskExecutor:
    codex_loop: Any

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        goal = str(node.metadata.get("goal") or node.task_profile or run.goal)
        result = self.codex_loop.run(goal)
        evidence_items = [asdict(item) for item in getattr(result.task.state, "evidence_items", [])]
        references = [item.get("path") or item.get("source", "") for item in evidence_items if item.get("path") or item.get("source")]
        status = NODE_STATUS_COMPLETED if result.status == "completed" else NODE_STATUS_FAILED
        return NodeResult(
            status=status,
            output={
                "summary": result.final_output or getattr(result.task, "summary", ""),
                "task_profile": result.task.task_profile,
                "evidence_items": evidence_items,
            },
            references=references,
            error=None if status == NODE_STATUS_COMPLETED else result.final_output,
        )
