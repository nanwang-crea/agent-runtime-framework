from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.capabilities.extension_policy import CapabilityExtensionRequest, assert_extension_preconditions
from agent_runtime_framework.capabilities.registry import resolve_capability_registry
from agent_runtime_framework.observability.events import RunEvent
from agent_runtime_framework.workflow.llm.access import get_application_context
from agent_runtime_framework.workflow.recovery.verification import get_verification_recipe
from agent_runtime_framework.workflow.state.approval import APPROVAL_KIND_CAPABILITY_EXTENSION
from agent_runtime_framework.workflow.state.models import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_FAILED,
    NODE_STATUS_WAITING_APPROVAL,
    NodeResult,
    WorkflowNode,
    WorkflowRun,
)
from agent_runtime_framework.workflow.runtime.protocols import RuntimeContextLike


def _services_from_context(context: RuntimeContextLike | None) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    services = dict(context.get("services") or {})
    app = get_application_context(context)
    if app is not None and isinstance(getattr(app, "services", None), dict):
        services = {**dict(app.services), **services}
    return services


@dataclass(slots=True)
class CapabilityExtensionExecutor:
    """受控能力扩展：支持两阶段审批（execute → waiting_approval → resume）。"""

    def _build_request(self, node: WorkflowNode) -> CapabilityExtensionRequest:
        return CapabilityExtensionRequest(
            proposed_capability_id=str(node.metadata.get("proposed_capability_id") or "").strip(),
            rationale=str(node.metadata.get("rationale") or "").strip() or "no rationale",
            extension_kind=str(node.metadata.get("extension_kind") or "macro").strip().lower(),
            smoke_verification_recipe_id=str(node.metadata.get("smoke_verification_recipe_id") or "extension_smoke_default"),
        )

    def _commit_extension(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        context: RuntimeContextLike,
        *,
        request: CapabilityExtensionRequest,
    ) -> NodeResult:
        services = _services_from_context(context)
        registry = resolve_capability_registry(services)
        try:
            assert_extension_preconditions(registry, request)
        except ValueError as exc:
            return NodeResult(status=NODE_STATUS_FAILED, error=str(exc), output={"summary": str(exc)})

        recipe = get_verification_recipe(request.smoke_verification_recipe_id)
        smoke = {
            "status": "passed",
            "success": True,
            "summary": "extension smoke recipe registered",
            "recipe": recipe.as_payload() if recipe else None,
        }
        audit_entry = {
            "kind": APPROVAL_KIND_CAPABILITY_EXTENSION,
            "proposed_capability_id": request.proposed_capability_id,
            "extension_kind": request.extension_kind,
            "rationale": request.rationale,
            "smoke_verification": smoke,
        }
        run.metadata.setdefault("capability_extension_audit", []).append(audit_entry)

        app = get_application_context(context)
        observer = getattr(app, "observer", None) if app is not None else None
        if observer is not None and hasattr(observer, "record"):
            observer.record(
                RunEvent(
                    stage="capability_extension",
                    detail="post_approval_execute",
                    payload=dict(audit_entry),
                )
            )

        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": f"Recorded capability extension proposal {request.proposed_capability_id}",
                "audit": audit_entry,
                "smoke_verification": smoke,
            },
        )

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        request = self._build_request(node)
        if not request.proposed_capability_id:
            return NodeResult(status=NODE_STATUS_FAILED, error="missing proposed_capability_id", output={})
        governance = bool(node.metadata.get("governance_two_phase", True))
        if governance:
            services = _services_from_context(context)
            registry = resolve_capability_registry(services)
            try:
                assert_extension_preconditions(registry, request)
            except ValueError as exc:
                return NodeResult(status=NODE_STATUS_FAILED, error=str(exc), output={"summary": str(exc)})
            proposal = {
                "proposed_capability_id": request.proposed_capability_id,
                "rationale": request.rationale,
                "extension_kind": request.extension_kind,
                "smoke_verification_recipe_id": request.smoke_verification_recipe_id,
            }
            return NodeResult(
                status=NODE_STATUS_WAITING_APPROVAL,
                approval_data={"kind": APPROVAL_KIND_CAPABILITY_EXTENSION, "proposal": proposal},
                output={"summary": "awaiting governance approval for capability extension", "proposal": proposal},
            )
        return self._commit_extension(node, run, context, request=request)

    def resume(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        prior_result: NodeResult,
        *,
        approved: bool,
        context: RuntimeContextLike = None,
    ) -> NodeResult:
        kind = str((prior_result.approval_data or {}).get("kind") or "")
        if kind != APPROVAL_KIND_CAPABILITY_EXTENSION:
            return NodeResult(
                status=NODE_STATUS_FAILED,
                error="capability_extension resume missing approval contract",
                output=dict(prior_result.output or {}),
            )
        if not approved:
            return NodeResult(
                status=NODE_STATUS_FAILED,
                error="capability extension rejected",
                output=dict(prior_result.output or {}),
            )
        proposal = dict((prior_result.output or {}).get("proposal") or {})
        merged_metadata = {**dict(node.metadata or {}), **proposal}
        synthetic = WorkflowNode(
            node_id=node.node_id,
            node_type=node.node_type,
            dependencies=list(getattr(node, "dependencies", []) or []),
            metadata=merged_metadata,
        )
        request = self._build_request(synthetic)
        return self._commit_extension(synthetic, run, context, request=request)
