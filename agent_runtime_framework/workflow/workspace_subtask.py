from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable
from uuid import uuid4

from agent_runtime_framework.agents.workspace_backend.models import EvidenceItem, TaskState, WorkspaceAction, WorkspaceTask
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_WAITING_APPROVAL, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class WorkspaceSubtaskResult:
    status: str
    final_output: str
    task: WorkspaceTask
    action_kind: str = "workspace_subtask"
    approval_request: Any | None = None
    resume_token: Any | None = None
    run_id: str = ""


@dataclass(slots=True)
class WorkspaceSubtaskExecutor:
    run_subtask: Callable[..., WorkspaceSubtaskResult] | None = None

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        goal = str(node.metadata.get("goal") or node.task_profile or run.goal)
        runner = self.run_subtask or self._default_run_subtask
        result = runner(goal, task_profile=str(node.task_profile or node.node_type), metadata=dict(node.metadata or {}))
        run.shared_state.setdefault("workspace_subtask_results", {})[node.node_id] = result
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
            return NodeResult(status="failed", error="Missing workspace subtask resume token")
        runner = self.run_subtask or self._default_run_subtask
        result = runner(str(node.metadata.get("goal") or run.goal), task_profile=str(node.task_profile or node.node_type), metadata={**dict(node.metadata or {}), "approved": approved, "resume_token": resume_token})
        run.shared_state.setdefault("workspace_subtask_results", {})[node.node_id] = result
        return self._to_node_result(node, result)

    def _default_run_subtask(self, goal: str, *, task_profile: str, metadata: dict[str, Any]) -> WorkspaceSubtaskResult:
        summary = str(metadata.get("summary") or goal)
        target_path = str(metadata.get("target_path") or metadata.get("path") or "").strip()
        state = TaskState()
        if target_path:
            state.evidence_items.append(EvidenceItem(source="workflow", kind="path", summary=target_path, path=target_path))
            state.resolved_target = target_path
        action = WorkspaceAction(kind="workspace_subtask", instruction=goal, status="completed", observation=summary, metadata={"direct_output": True})
        task = WorkspaceTask(goal=goal, actions=[action], task_profile=task_profile, state=state)
        task.summary = summary
        return WorkspaceSubtaskResult(status="completed", final_output=summary, task=task, action_kind="workspace_subtask", run_id=str(uuid4()))

    def _to_node_result(self, node: WorkflowNode, result: WorkspaceSubtaskResult) -> NodeResult:
        verification = getattr(getattr(result, "task", None), "verification", None)
        verification_payload = asdict(verification) if verification is not None else None
        evidence_items = [asdict(item) for item in getattr(result.task.state, "evidence_items", [])]
        references = [item.get("path") or item.get("source", "") for item in evidence_items if item.get("path") or item.get("source")]
        output = {
            "summary": result.final_output or getattr(result.task, "summary", ""),
            "task_profile": result.task.task_profile,
            "evidence_items": evidence_items,
            "workspace_status": result.status,
            "fallback_reason": str(node.metadata.get("fallback_reason") or "workflow_subtask"),
            "compatibility_mode": str(node.metadata.get("compatibility_mode") or "workflow_subtask"),
            "source_loop": str(node.metadata.get("source_loop") or "workflow_subtask"),
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
