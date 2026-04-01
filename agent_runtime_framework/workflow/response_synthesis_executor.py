from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ResponseSynthesisExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        node_results = dict(run.shared_state.get("node_results") or {})
        resolution = next((result.output for result in node_results.values() if isinstance(getattr(result, "output", None), dict) and "resolution_status" in result.output), {})
        inspection = next((result.output for result in node_results.values() if isinstance(getattr(result, "output", None), dict) and ("resolved_kind" in result.output or result.output.get("path"))), {})
        references: list[str] = []
        for result in node_results.values():
            for reference in getattr(result, "references", []) or []:
                if reference not in references:
                    references.append(reference)

        if bool(inspection.get("clarification_required") or resolution.get("clarification_required")):
            summary = str(inspection.get("text") or inspection.get("summary") or resolution.get("text") or resolution.get("summary") or "Please clarify the target.")
            return NodeResult(status=NODE_STATUS_COMPLETED, output={"summary": summary, "clarification_required": True}, references=references)

        path = str(inspection.get("path") or resolution.get("resolved_path") or "").strip()
        detail = str(inspection.get("text") or inspection.get("summary") or resolution.get("summary") or "")
        fallback_summary = f"{path}\n{detail}".strip() if path else detail
        if not fallback_summary:
            fallback_summary = str(node.metadata.get("default_summary") or run.goal)
        summary = synthesize_text(
            context,
            role="composer",
            system_prompt=(
                "You synthesize workflow findings into a user-facing answer. "
                "Answer in the user's language, directly address the goal, and avoid bullet spam unless necessary."
            ),
            payload={
                "goal": run.goal,
                "resolved_target": resolution,
                "inspection": inspection,
                "references": references,
            },
            max_tokens=320,
        ) or fallback_summary
        run.shared_state["response_synthesis"] = {"summary": summary, "final_response": summary}
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"summary": summary}, references=references)
