from __future__ import annotations

from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NodeResult


def aggregate_node_results(results: list[NodeResult]) -> NodeResult:
    summaries: list[str] = []
    references: list[str] = []
    for result in results:
        summary = result.output.get("summary") if isinstance(result.output, dict) else None
        if summary:
            summaries.append(summary)
        for reference in result.references:
            if reference not in references:
                references.append(reference)
    return NodeResult(
        status=NODE_STATUS_COMPLETED,
        output={"summaries": summaries},
        references=references,
    )
