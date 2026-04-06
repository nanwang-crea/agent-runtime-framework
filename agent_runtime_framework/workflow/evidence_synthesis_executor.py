from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike

from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun, normalize_aggregated_workflow_payload


@dataclass(slots=True)
class EvidenceSynthesisExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        aggregated = run.shared_state.get("aggregated_result")
        if aggregated is None:
            node_results = run.shared_state.get("node_results", {})
            for key, result in node_results.items():
                if key == node.node_id:
                    continue
                if isinstance(result.output, dict) and any(field in result.output for field in ("facts", "evidence_items", "summaries")):
                    aggregated = result
        aggregated_output = normalize_aggregated_workflow_payload(aggregated.output if isinstance(getattr(aggregated, "output", None), dict) else {})
        facts = list(aggregated_output.get("facts", []) or [])
        evidence_items = list(aggregated_output.get("evidence_items", []) or [])
        chunks = list(aggregated_output.get("chunks", []) or [])
        open_questions = list(aggregated_output.get("open_questions", []) or [])
        summaries = list(aggregated_output.get("summaries", []) or [])
        single_summary = str(aggregated_output.get("summary") or "").strip()
        if single_summary and single_summary not in summaries:
            summaries.append(single_summary)
        references = list(getattr(aggregated, "references", []) or [])
        include_path = bool(run.shared_state.get("resolved_target")) or _looks_like_explicit_target(run.goal)
        summary = synthesize_text(
            context,
            role="composer",
            system_prompt=(
                "You synthesize workflow evidence for an end user. "
                "Summarize the most important findings clearly and concisely in the user's language."
            ),
            payload={
                "goal": run.goal,
                "facts": facts,
                "evidence_items": evidence_items,
                "chunks": chunks,
                "summaries": summaries,
                "references": references,
                "open_questions": open_questions,
                "include_path": include_path,
            },
            max_tokens=260,
        )
        if summary is None:
            raise RuntimeError("composer model unavailable for evidence_synthesis summary")
        output = {
            "summary": summary,
            "facts": facts,
            "chunks": chunks,
            "evidence_items": evidence_items,
            "open_questions": open_questions,
            "references": references,
        }
        run.shared_state["evidence_synthesis"] = output
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output=output,
            references=references,
        )

def _looks_like_explicit_target(text: str) -> bool:
    value = str(text or "").strip()
    return bool(value) and " " not in value and "\n" not in value and ("/" in value or "." in value)
