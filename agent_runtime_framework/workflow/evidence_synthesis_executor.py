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
        if chunks:
            summary = self._chunk_fallback(chunks, evidence_items, include_path=include_path)
        else:
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
                    "summaries": summaries,
                    "references": references,
                    "open_questions": open_questions,
                },
                max_tokens=260,
            ) or self._fallback_summary(facts, summaries, evidence_items)
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

    def _fallback_summary(self, facts: list[dict[str, Any]], summaries: list[str], evidence_items: list[dict[str, Any]]) -> str:
        if summaries:
            return "\n".join(str(item) for item in summaries if item)
        if facts:
            return "；".join(f"{fact.get('kind')}: {fact.get('path')}" for fact in facts[:6])
        if evidence_items:
            return "；".join(str(item.get("summary") or item.get("path") or "") for item in evidence_items[:6])
        return "No synthesized evidence available."

    def _chunk_fallback(self, chunks: list[dict[str, Any]], evidence_items: list[dict[str, Any]], *, include_path: bool) -> str:
        texts = [str(chunk.get("text") or "").rstrip() for chunk in chunks if str(chunk.get("text") or "").strip()]
        if not texts:
            return "No synthesized evidence available."
        path_hint = ""
        if include_path and evidence_items and isinstance(evidence_items[0], dict):
            path_hint = str(evidence_items[0].get("relative_path") or evidence_items[0].get("path") or "").strip()
        if include_path and not path_hint and chunks and isinstance(chunks[0], dict):
            path_hint = str(chunks[0].get("relative_path") or chunks[0].get("path") or "").strip()
        if len(texts) == 1:
            return f"{path_hint}\n{texts[0]}".strip() if path_hint else texts[0]
        body = f"{texts[0]}\n...[已截断]\n{texts[-1]}"
        return f"{path_hint}\n{body}".strip() if path_hint else body


def _looks_like_explicit_target(text: str) -> bool:
    value = str(text or "").strip()
    return bool(value) and " " not in value and "\n" not in value and ("/" in value or "." in value)
