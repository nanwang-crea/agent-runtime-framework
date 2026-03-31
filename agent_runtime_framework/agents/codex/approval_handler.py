from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from agent_runtime_framework.assistant.approval import ApprovalRequest, ResumeToken

if TYPE_CHECKING:
    from agent_runtime_framework.agents.codex.loop import CodexAgentLoop, CodexAgentLoopResult
    from agent_runtime_framework.agents.codex.models import CodexAction, CodexActionResult, CodexTask
    from agent_runtime_framework.assistant.session import AssistantSession


@dataclass(slots=True)
class PendingCodexApproval:
    session: "AssistantSession"
    task: "CodexTask"
    action_index: int
    request: ApprovalRequest


def resume_pending_approval(loop: "CodexAgentLoop", token: ResumeToken, *, approved: bool) -> "CodexAgentLoopResult":
    pending = loop._pending_approvals.pop(token.token_id, None)
    if pending is None or not approved:
        task = pending.task if pending is not None else loop._new_task("", session=loop._require_session())
        return loop._result_type(
            status="cancelled",
            final_output="approval was rejected or expired",
            task=task,
            run_id=str(uuid4()),
        )
    loop.context.session = pending.session
    pending.task.actions[pending.action_index].metadata["_approval_granted"] = True
    pending.task.actions[pending.action_index].status = "pending"
    return loop._execute_task(pending.task, pending.session, start_index=pending.action_index)


def maybe_pause_for_approval(
    loop: "CodexAgentLoop",
    task: "CodexTask",
    action_index: int,
    action: "CodexAction",
    session: "AssistantSession",
) -> tuple[ApprovalRequest, ResumeToken] | None:
    if bool(action.metadata.get("_approval_granted")):
        return None
    if action.risk_class not in {"high", "destructive"}:
        return None
    return store_approval(
        loop,
        task,
        action_index,
        action,
        session,
        reason=f"action '{action.kind}' requires confirmation",
        risk_class=action.risk_class,
    )


def pause_for_result_approval(
    loop: "CodexAgentLoop",
    task: "CodexTask",
    action_index: int,
    action: "CodexAction",
    result: "CodexActionResult",
    session: "AssistantSession",
) -> tuple[ApprovalRequest, ResumeToken]:
    return store_approval(
        loop,
        task,
        action_index,
        action,
        session,
        reason=result.approval_reason or f"action '{action.kind}' requires confirmation",
        risk_class=result.risk_class or action.risk_class or "high",
    )


def store_approval(
    loop: "CodexAgentLoop",
    task: "CodexTask",
    action_index: int,
    action: "CodexAction",
    session: "AssistantSession",
    *,
    reason: str,
    risk_class: str,
) -> tuple[ApprovalRequest, ResumeToken]:
    request = ApprovalRequest(
        capability_name=action.kind,
        instruction=action.instruction,
        reason=reason,
        risk_class=risk_class,
    )
    token = ResumeToken(
        token_id=str(uuid4()),
        session_id=session.session_id,
        plan_id=task.task_id,
        step_index=action_index,
    )
    loop._pending_approvals[token.token_id] = PendingCodexApproval(
        session=session,
        task=task,
        action_index=action_index,
        request=request,
    )
    action.status = "awaiting_approval"
    return request, token
