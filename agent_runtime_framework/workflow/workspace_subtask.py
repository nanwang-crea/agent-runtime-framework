from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_WAITING_APPROVAL, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class WorkspaceSubtaskExecutor:
    workspace_loop: Any

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        goal = str(node.metadata.get("goal") or node.task_profile or run.goal)
        result = self.workspace_loop.run(goal)
        run.shared_state.setdefault("workspace_loop_results", {})[node.node_id] = result
        return self._to_node_result(node, result)

    def resume(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        prior_result: NodeResult | None,
        *,
        approved: bool,
        context: dict[str, Any] | None = None,
    ) -> NodeResult:
        approval_data = dict((prior_result.approval_data if prior_result is not None else {}) or {})
        resume_token = approval_data.get("resume_token")
        if resume_token is None:
            return NodeResult(status="failed", error="Missing codex resume token")
        result = self.workspace_loop.resume(resume_token, approved=approved)
        run.shared_state.setdefault("workspace_loop_results", {})[node.node_id] = result
        return self._to_node_result(node, result)

    def _to_node_result(self, node: WorkflowNode, result: Any) -> NodeResult:
        verification = getattr(getattr(result, "task", None), "verification", None)
        verification_payload = asdict(verification) if verification is not None else None
        evidence_items = [asdict(item) for item in getattr(result.task.state, "evidence_items", [])]
        references = [item.get("path") or item.get("source", "") for item in evidence_items if item.get("path") or item.get("source")]
        output = {
            "summary": result.final_output or getattr(result.task, "summary", ""),
            "task_profile": result.task.task_profile,
            "evidence_items": evidence_items,
            "workspace_status": result.status,
            "fallback_reason": str(node.metadata.get("fallback_reason") or "legacy_bridge"),
            "compatibility_mode": str(node.metadata.get("compatibility_mode") or "workspace_loop_bridge"),
            "source_loop": str(node.metadata.get("source_loop") or type(self.workspace_loop).__name__),
        }
        if verification_payload is not None:
            output["verification"] = verification_payload
        if result.resume_token is not None:
            return NodeResult(
                status=NODE_STATUS_WAITING_APPROVAL,
                output=output,
                references=references,
                approval_data={
                    "kind": "workspace_subtask",
                    "resume_token": result.resume_token,
                    "approval_request": result.approval_request,
                },
                error=None,
            )
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output=output,
            references=references,
            error=None,
        )
