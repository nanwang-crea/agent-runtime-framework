from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_runtime_framework.workflow import WorkflowRun


PayloadFn = Callable[[], Any]
TraceFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


@dataclass(slots=True)
class WorkflowPayloadBuilder:
    session_payload: PayloadFn
    plan_history_payload: PayloadFn
    run_history_payload: PayloadFn
    memory_payload: PayloadFn
    context_payload: PayloadFn
    with_router_trace: TraceFn
    workspace: str

    def build(self, run: WorkflowRun, *, resume_token_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
        execution_trace = [
            {"name": node.node_type, "status": run.node_states[node.node_id].status, "detail": node.node_type}
            for node in run.graph.nodes
            if node.node_id in run.node_states
        ]
        approval_request = self._workflow_approval_request(run) if run.status == "waiting_approval" else None
        clarification_request = run.shared_state.get("clarification_request")
        payload_status = "needs_clarification" if clarification_request is not None and run.status == "completed" else run.status
        pending_clarification = ({**dict(clarification_request or {}), "run_id": run.run_id} if payload_status == "needs_clarification" else None)
        final_answer = str(run.final_output or (clarification_request or {}).get("prompt") or (approval_request or {}).get("reason") or "")
        evidence = self._workflow_evidence_payload(run)
        graph_state = dict(run.metadata.get("agent_graph_state") or {})
        judge_history = list(graph_state.get("judge_history") or [])
        payload = {
            "status": payload_status,
            "run_id": run.run_id,
            "plan_id": run.run_id,
            "final_answer": final_answer,
            "capability_name": ("conversation" if any(node.node_type == "conversation_response" for node in run.graph.nodes) or run.graph.metadata.get("conversation_mode") else "workflow"),
            "runtime": "workflow",
            "execution_trace": self.with_router_trace(execution_trace),
            "evidence": evidence,
            "approval_request": approval_request,
            "resume_token_id": resume_token_id,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": self.workspace,
            "judge": judge_history[-1] if judge_history else None,
            "planned_subgraphs": list(graph_state.get("planned_subgraphs") or []),
            "graph_state_summary": {"current_iteration": graph_state.get("current_iteration", 0), "appended_node_ids": list(graph_state.get("appended_node_ids") or [])},
            "append_history": list(run.graph.metadata.get("append_history") or run.metadata.get("append_history") or []),
            "root_graph": dict(run.metadata.get("root_graph") or {}),
        }
        return payload, pending_clarification

    def _workflow_approval_request(self, run: Any) -> dict[str, Any] | None:
        resume_token = run.shared_state.get("resume_token")
        if resume_token is None:
            return None
        state = run.node_states.get(resume_token.node_id)
        if state is None or state.result is None:
            return {"capability_name": "approval_gate", "instruction": "Review workflow step", "reason": "需要审批后继续执行工作流。", "risk_class": "medium"}
        approval_data = dict(state.result.approval_data or {})
        request = approval_data.get("approval_request")
        if request is not None:
            return {
                "capability_name": request.capability_name,
                "instruction": request.instruction,
                "reason": request.reason,
                "risk_class": request.risk_class,
            }
        return {
            "capability_name": state.node_id if hasattr(state, "node_id") else "approval_gate",
            "instruction": str(state.result.output.get("summary") if isinstance(state.result.output, dict) else "Review workflow step"),
            "reason": "需要审批后继续执行工作流。",
            "risk_class": "medium",
        }

    def _workflow_evidence_payload(self, run: Any) -> dict[str, Any]:
        node_results = run.shared_state.get("node_results", {})
        aggregated = run.shared_state.get("aggregated_result")
        aggregated_output = aggregated.output if isinstance(getattr(aggregated, "output", None), dict) else {}
        synthesized = dict(run.shared_state.get("evidence_synthesis") or {})
        candidates: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        verification = dict(synthesized.get("verification") or aggregated_output.get("verification") or {})
        if not verification:
            verification = {"status": "not_run", "success": False, "summary": "No explicit verification result was produced."}
        for result in node_results.values():
            output = result.output if isinstance(getattr(result, "output", None), dict) else {}
            candidates.extend(item for item in output.get("evidence_items", []) if isinstance(item, dict))
            chunks.extend(item for item in output.get("chunks", []) if isinstance(item, dict))
        if isinstance(aggregated_output.get("evidence_items"), list):
            candidates = [item for item in aggregated_output.get("evidence_items", []) if isinstance(item, dict)]
        if isinstance(aggregated_output.get("chunks"), list):
            chunks = [item for item in aggregated_output.get("chunks", []) if isinstance(item, dict)]
        return {"candidates": candidates, "chunks": chunks, "verification": verification}
