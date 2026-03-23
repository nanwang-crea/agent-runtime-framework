from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

if TYPE_CHECKING:
    from agent_runtime_framework.assistant.capabilities import CapabilitySpec
    from agent_runtime_framework.assistant.session import AssistantSession, ExecutionPlan, PlannedAction


@dataclass(slots=True)
class ApprovalRequest:
    capability_name: str
    instruction: str
    reason: str
    risk_class: str


@dataclass(slots=True)
class ResumeToken:
    token_id: str
    session_id: str
    plan_id: str
    step_index: int


@dataclass(slots=True)
class PendingApproval:
    session: AssistantSession
    plan: ExecutionPlan
    step_index: int
    request: ApprovalRequest


class PendingApprovalStore(Protocol):
    def put(self, token_id: str, pending: PendingApproval) -> None: ...

    def get(self, token_id: str) -> PendingApproval | None: ...

    def pop(self, token_id: str) -> PendingApproval | None: ...


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    def put(self, token_id: str, pending: PendingApproval) -> None:
        self._pending[token_id] = pending

    def get(self, token_id: str) -> PendingApproval | None:
        return self._pending.get(token_id)

    def pop(self, token_id: str) -> PendingApproval | None:
        return self._pending.pop(token_id, None)


class ApprovalManager:
    def __init__(self, *, store: PendingApprovalStore | None = None) -> None:
        self._store = store or InMemoryApprovalStore()

    def request_for(
        self,
        session: AssistantSession,
        plan: ExecutionPlan,
        step_index: int,
        step: PlannedAction,
        capability: CapabilitySpec,
    ) -> tuple[ApprovalRequest, ResumeToken] | None:
        if capability.risk_class not in {"high", "destructive"}:
            return None
        return self.create_request(
            session=session,
            plan=plan,
            step_index=step_index,
            capability_name=capability.name,
            instruction=step.instruction,
            reason=f"capability '{capability.name}' requires confirmation",
            risk_class=capability.risk_class,
        )

    def create_request(
        self,
        *,
        session: AssistantSession,
        plan: ExecutionPlan,
        step_index: int,
        capability_name: str,
        instruction: str,
        reason: str,
        risk_class: str,
    ) -> tuple[ApprovalRequest, ResumeToken]:
        request = ApprovalRequest(
            capability_name=capability_name,
            instruction=instruction,
            reason=reason,
            risk_class=risk_class,
        )
        token = ResumeToken(
            token_id=str(uuid4()),
            session_id=session.session_id,
            plan_id=plan.plan_id,
            step_index=step_index,
        )
        self._store.put(
            token.token_id,
            PendingApproval(
                session=session,
                plan=plan,
                step_index=step_index,
                request=request,
            ),
        )
        return request, token

    def resolve(self, token: ResumeToken, approved: bool) -> PendingApproval | None:
        pending = self._store.pop(token.token_id)
        if pending is None or not approved:
            return None
        return pending
