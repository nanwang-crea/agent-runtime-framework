from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_runtime_framework.workflow.aggregator import aggregate_node_results
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
            if path.is_dir():
                for child in sorted(path.iterdir(), key=lambda item: item.name)[:3]:
                    child_label = f"{path.name}/{child.name}/" if child.is_dir() else f"{path.name}/{child.name}"
                    entries.append(child_label)
                    references.append(str(child))
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
        truncated = len(content) > self.max_chars
        visible_content = content[: self.max_chars]
        summary = visible_content[:200]
        if truncated:
            visible_content = f"{visible_content.rstrip()}\n...[已截断]"
            summary = f"{summary.rstrip()} ...[已截断]"
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"path": target_path, "content": visible_content, "summary": summary},
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
class VerificationExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        node_results = run.shared_state.get("node_results", {})
        latest_verification: dict[str, Any] | None = None
        references: list[str] = []
        for key, result in node_results.items():
            if key == node.node_id:
                continue
            if isinstance(result.output, dict):
                verification = result.output.get("verification")
                if isinstance(verification, dict):
                    latest_verification = verification
            for reference in result.references:
                if reference not in references:
                    references.append(reference)
        if latest_verification is None:
            summary = str(node.metadata.get("verification_summary") or "No explicit verification result was produced.")
            return NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={"summary": summary, "verification": {"success": True, "summary": summary}},
                references=references,
            )
        success = bool(latest_verification.get("success", False))
        summary = str(latest_verification.get("summary") or "Verification completed.")
        return NodeResult(
            status=NODE_STATUS_COMPLETED if success else NODE_STATUS_FAILED,
            output={"summary": summary, "verification": latest_verification},
            references=references,
            error=None if success else summary,
        )


@dataclass(slots=True)
class ApprovalGateExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        summary = str(node.metadata.get("approval_summary") or "Approval gate passed.")
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"summary": summary},
            references=[],
        )


@dataclass(slots=True)
class FinalResponseExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        aggregated = run.shared_state.get("aggregated_result")
        if aggregated is None:
            node_results = run.shared_state.get("node_results", {})
            direct_results = [
                result
                for key, result in node_results.items()
                if key != node.node_id and result.status == NODE_STATUS_COMPLETED
            ]
            aggregated = aggregate_node_results(direct_results)
        summaries = aggregated.output.get("summaries", []) if aggregated else []
        final_response = "\n".join(str(item) for item in summaries if item)
        result = NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"final_response": final_response},
            references=list(aggregated.references if aggregated else []),
        )
        run.final_output = final_response
        return result
