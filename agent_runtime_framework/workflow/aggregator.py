from __future__ import annotations

from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NodeResult


def aggregate_node_results(results: list[NodeResult]) -> NodeResult:
    summaries: list[str] = []
    facts: list[dict] = []
    evidence_items: list[dict] = []
    chunks: list[dict] = []
    open_questions: list[str] = []
    verification_events: list[dict] = []
    artifacts: dict[str, list] = {}
    references: list[str] = []
    for result in results:
        output = result.output if isinstance(result.output, dict) else {}
        summary = output.get("summary")
        if summary:
            summaries.append(summary)
        for fact in output.get("facts", []) or []:
            if isinstance(fact, dict) and fact not in facts:
                facts.append(fact)
        for item in output.get("evidence_items", []) or []:
            if isinstance(item, dict) and item not in evidence_items:
                evidence_items.append(item)
        for chunk in output.get("chunks", []) or []:
            if isinstance(chunk, dict) and chunk not in chunks:
                chunks.append(chunk)
        for question in output.get("open_questions", []) or []:
            if question and question not in open_questions:
                open_questions.append(str(question))
        verification = output.get("verification")
        if isinstance(verification, dict):
            verification_events.append(verification)
        for key, value in (output.get("artifacts") or {}).items():
            if key not in artifacts:
                artifacts[key] = []
            values = value if isinstance(value, list) else [value]
            for entry in values:
                if entry not in artifacts[key]:
                    artifacts[key].append(entry)
        for reference in result.references:
            if reference not in references:
                references.append(reference)
    return NodeResult(
        status=NODE_STATUS_COMPLETED,
        output={
            "summaries": summaries,
            "facts": facts,
            "evidence_items": evidence_items,
            "chunks": chunks,
            "artifacts": artifacts,
            "open_questions": open_questions,
            "verification": verification_events[-1] if verification_events else None,
            "verification_events": verification_events,
        },
        references=references,
    )
