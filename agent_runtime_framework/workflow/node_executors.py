from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_runtime_framework.workflow.aggregator import aggregate_node_results
from agent_runtime_framework.workflow.codex_subtask import CodexSubtaskExecutor
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


class NodeExecutor(Protocol):
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult: ...


@dataclass(slots=True)
class WorkspaceOverviewExecutor:
    max_entries: int = 50

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        workspace_root = Path(str((context or {}).get("workspace_root", ".")))
        entries = []
        references = []
        for path in sorted(workspace_root.iterdir(), key=lambda item: item.name)[: self.max_entries]:
            label = f"{path.name}/" if path.is_dir() else path.name
            entries.append(label)
            references.append(str(path))
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"workspace_root": str(workspace_root), "entries": entries, "summary": ", ".join(entries[:5])},
            references=references,
        )


@dataclass(slots=True)
class FileReadExecutor:
    max_chars: int = 4000

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        workspace_root = Path(str((context or {}).get("workspace_root", ".")))
        target_path = str(node.metadata.get("target_path") or "")
        if not target_path:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing target_path")

        path = workspace_root / target_path
        content = path.read_text(encoding="utf-8")
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"path": target_path, "content": content[: self.max_chars], "summary": content[:200]},
            references=[str(path)],
        )


@dataclass(slots=True)
class AggregationExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        node_results = run.shared_state.get("node_results", {})
        ordered_results = [
            result
            for key, result in node_results.items()
            if key != node.node_id and result.status == NODE_STATUS_COMPLETED
        ]
        aggregated = aggregate_node_results(ordered_results)
        run.shared_state["aggregated_result"] = aggregated
        return aggregated


@dataclass(slots=True)
class FinalResponseExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        aggregated = run.shared_state.get("aggregated_result")
        summaries = aggregated.output.get("summaries", []) if aggregated else []
        final_response = "\n".join(str(item) for item in summaries if item)
        result = NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"final_response": final_response},
            references=list(aggregated.references if aggregated else []),
        )
        run.final_output = final_response
        return result
