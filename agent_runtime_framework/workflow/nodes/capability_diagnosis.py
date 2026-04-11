from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.capabilities.registry import resolve_capability_registry
from agent_runtime_framework.workflow.llm.access import get_application_context
from agent_runtime_framework.workflow.recovery.models import normalize_recovery_mode
from agent_runtime_framework.workflow.state.models import NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.runtime.protocols import RuntimeContextLike


def _services_from_context(context: RuntimeContextLike | None) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    services = dict(context.get("services") or {})
    app = get_application_context(context)
    if app is not None and isinstance(getattr(app, "services", None), dict):
        services = {**dict(app.services), **services}
    return services


def _latest_tool_failure_summary(run: WorkflowRun) -> dict[str, Any]:
    node_results = run.shared_state.get("node_results") or {}
    last: dict[str, Any] | None = None
    for _node_id, result in node_results.items():
        if not hasattr(result, "output") or not isinstance(result.output, dict):
            continue
        out = result.output
        if out.get("tool_metadata") and not out.get("tool_output"):
            meta = dict(out.get("tool_metadata") or {})
            err = meta.get("error") if isinstance(meta.get("error"), dict) else {}
            last = {
                "tool_name": out.get("tool_name"),
                "code": err.get("code"),
                "failure_category": meta.get("failure_category") or err.get("failure_category"),
                "message": out.get("tool_error") or err.get("message"),
            }
    return last or {}


@dataclass(slots=True)
class CapabilityDiagnosisExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        services = _services_from_context(context)
        registry = resolve_capability_registry(services)
        graph_state = run.shared_state.get("agent_graph_state_ref")
        failure_diagnosis: dict[str, Any] = {}
        if graph_state is not None and graph_state.failure_history:
            last_fail = dict(graph_state.failure_history[-1])
            raw = last_fail.get("failure_diagnosis")
            if isinstance(raw, dict):
                failure_diagnosis = dict(raw)

        tool_hint = _latest_tool_failure_summary(run)
        matched = registry.match_failure(failure_diagnosis)

        missing_capability = str(failure_diagnosis.get("missing_capability") or "").strip()
        if not missing_capability and tool_hint.get("failure_category") == "tool_execution":
            missing_capability = "unknown_tool_failure"

        preferred = [str(x).strip() for x in (node.metadata.get("preferred_capability_ids") or []) if str(x).strip()]
        for cap in matched:
            if cap not in preferred:
                preferred.append(cap)

        recoverable = bool(failure_diagnosis.get("recoverable", True))
        human_handoff_required = bool(node.metadata.get("human_handoff_required", False))
        recovery_mode = normalize_recovery_mode(
            failure_diagnosis.get("suggested_recovery_mode") or "",
            default="collect_more_evidence",
        )
        if preferred and not missing_capability:
            recovery_mode = normalize_recovery_mode("compose_capability", default=recovery_mode)
        elif missing_capability and not registry.has(str(missing_capability)):
            recovery_mode = normalize_recovery_mode("extend_capability", default=recovery_mode)

        output = {
            "missing_capability": missing_capability or None,
            "preferred_capability_ids": preferred[:8],
            "recovery_mode": recovery_mode,
            "human_handoff_required": human_handoff_required,
            "failure_diagnosis": failure_diagnosis,
            "latest_tool_failure": tool_hint or None,
            "available_capabilities_count": len(registry.list_payloads()),
            "quality_signals": [
                {
                    "source": "capability_diagnosis",
                    "relevance": "high",
                    "confidence": 0.85,
                    "progress_contribution": "capability_gap_assessed",
                    "verification_needed": False,
                    "recoverable_error": recoverable,
                }
            ],
            "reasoning_trace": [{"kind": "capability_diagnosis", "summary": "Assessed capability gaps vs registry"}],
        }
        return NodeResult(status=NODE_STATUS_COMPLETED, output=output)
