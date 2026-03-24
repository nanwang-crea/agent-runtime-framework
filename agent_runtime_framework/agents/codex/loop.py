from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.conversation import stream_conversation_reply
from agent_runtime_framework.assistant.approval import ApprovalRequest, ResumeToken
from agent_runtime_framework.assistant.session import AssistantSession
from agent_runtime_framework.agents.codex.models import (
    CodexAction,
    CodexActionResult,
    CodexEvaluationDecision,
    CodexTask,
    VerificationResult,
)
from agent_runtime_framework.agents.codex.planner import plan_next_codex_action
from agent_runtime_framework.tools import ToolCall, execute_tool_call


@dataclass(slots=True)
class CodexContext:
    application_context: ApplicationContext
    services: dict[str, Any] = field(default_factory=dict)
    session: AssistantSession | None = None


@dataclass(slots=True)
class CodexAgentLoopResult:
    status: str
    final_output: str
    task: CodexTask
    action_kind: str = ""
    approval_request: ApprovalRequest | None = None
    resume_token: ResumeToken | None = None
    run_id: str = ""


@dataclass(slots=True)
class _PendingCodexApproval:
    session: AssistantSession
    task: CodexTask
    action_index: int
    request: ApprovalRequest


class CodexAgentLoop:
    def __init__(self, context: CodexContext) -> None:
        self.context = context
        self._pending_approvals: dict[str, _PendingCodexApproval] = {}

    def run(self, user_input: str) -> CodexAgentLoopResult:
        session = self._require_session()
        session.add_turn("user", user_input)
        task = self._build_task(user_input, session)
        result = self._execute_task(task, session, start_index=0)
        if result.status == "completed":
            session.add_turn("assistant", result.final_output)
        return result

    def resume(self, token: ResumeToken, *, approved: bool) -> CodexAgentLoopResult:
        pending = self._pending_approvals.pop(token.token_id, None)
        if pending is None or not approved:
            task = pending.task if pending is not None else CodexTask(goal="", actions=[])
            return CodexAgentLoopResult(
                status="cancelled",
                final_output="approval was rejected or expired",
                task=task,
                run_id=str(uuid4()),
            )
        self.context.session = pending.session
        pending.task.actions[pending.action_index].metadata["_approval_granted"] = True
        pending.task.actions[pending.action_index].status = "pending"
        return self._execute_task(pending.task, pending.session, start_index=pending.action_index)

    def _require_session(self) -> AssistantSession:
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        return session

    def _build_task(self, user_input: str, session: AssistantSession) -> CodexTask:
        planner = self.context.services.get("action_planner")
        if callable(planner):
            planned = planner(user_input, session, self.context)
            if isinstance(planned, CodexTask):
                return planned
            if isinstance(planned, list):
                return CodexTask(goal=user_input, actions=[self._normalize_action(item) for item in planned])
        return CodexTask(goal=user_input, actions=[])

    def _normalize_action(self, action: Any) -> CodexAction:
        if isinstance(action, CodexAction):
            return action
        if isinstance(action, dict):
            return CodexAction(
                kind=str(action.get("kind") or ""),
                instruction=str(action.get("instruction") or ""),
                risk_class=str(action.get("risk_class") or "low"),
                metadata=dict(action.get("metadata") or {}),
            )
        raise TypeError(f"unsupported action type: {type(action)!r}")

    def _execute_task(
        self,
        task: CodexTask,
        session: AssistantSession,
        *,
        start_index: int,
    ) -> CodexAgentLoopResult:
        run_id = str(uuid4())
        final_output = ""
        last_kind = ""
        task.status = "running"
        action_index = max(0, start_index)
        while True:
            next_index = self._next_action_index(task, start_index=action_index)
            if next_index is None:
                planned = self._plan_next_action(task, session)
                if planned is None:
                    break
                task.actions.append(planned)
                next_index = len(task.actions) - 1
            action = task.actions[next_index]
            approval = self._maybe_pause_for_approval(task, next_index, action, session)
            if approval is not None:
                return CodexAgentLoopResult(
                    status="needs_approval",
                    final_output=approval[0].reason,
                    task=task,
                    action_kind=action.kind,
                    approval_request=approval[0],
                    resume_token=approval[1],
                    run_id=run_id,
                )
            result = self._execute_action(action, session)
            if result.artifacts:
                task.artifact_ids.extend(self._persist_artifacts(task, action, result.artifacts, run_id))
            if result.artifact_ids:
                task.artifact_ids.extend(result.artifact_ids)
            if result.needs_approval:
                approval = self._pause_for_result_approval(task, next_index, action, result, session)
                action.status = "awaiting_approval"
                action.observation = result.final_output or approval[0].reason
                return CodexAgentLoopResult(
                    status="needs_approval",
                    final_output=action.observation,
                    task=task,
                    action_kind=action.kind,
                    approval_request=approval[0],
                    resume_token=approval[1],
                    run_id=run_id,
                )
            action.status = result.status
            action.observation = result.final_output
            action.metadata["result"] = dict(result.metadata)
            verification_payload = dict(result.metadata.get("verification") or {})
            if verification_payload:
                action.metadata["verification_result"] = VerificationResult(
                    success=bool(verification_payload.get("success")),
                    summary=str(verification_payload.get("summary") or result.final_output),
                    evidence=[str(verification_payload.get("command") or "")],
                )
            final_output = result.final_output
            last_kind = action.kind
            session.focused_capability = action.kind
            if result.status != "completed":
                task.status = result.status
                return CodexAgentLoopResult(
                    status=result.status,
                    final_output=result.final_output,
                    task=task,
                    action_kind=action.kind,
                    run_id=run_id,
                )
            action_index = next_index + 1
        task.status = "completed"
        task.verification = self._build_verification(task, final_output)
        return CodexAgentLoopResult(
            status="completed",
            final_output=final_output,
            task=task,
            action_kind=last_kind,
            run_id=run_id,
        )

    def _next_action_index(self, task: CodexTask, *, start_index: int) -> int | None:
        for index in range(max(0, start_index), len(task.actions)):
            if task.actions[index].status == "pending":
                return index
        return None

    def _plan_next_action(self, task: CodexTask, session: AssistantSession) -> CodexAction | None:
        evaluator = self.context.services.get("output_evaluator")
        if callable(evaluator) and any(action.status == "completed" for action in task.actions):
            decision = self._normalize_evaluation_decision(
                evaluator(task, session, self.context, list(self.context.application_context.tools.names()))
            )
            if decision.status == "finish":
                return None
            if decision.next_action is not None:
                return decision.next_action
        planner = self.context.services.get("next_action_planner")
        if callable(planner):
            planned = planner(task, session, self.context, list(self.context.application_context.tools.names()))
            if planned is None:
                return None
            return self._normalize_action(planned)
        return plan_next_codex_action(task, session, self.context)

    def _normalize_evaluation_decision(self, decision: Any) -> CodexEvaluationDecision:
        if isinstance(decision, CodexEvaluationDecision):
            if decision.next_action is not None:
                decision.next_action = self._normalize_action(decision.next_action)
            return decision
        if isinstance(decision, CodexAction):
            return CodexEvaluationDecision(status="continue", next_action=decision)
        if isinstance(decision, dict):
            status = str(decision.get("status") or "abstain")
            next_action = decision.get("next_action")
            return CodexEvaluationDecision(
                status=status,
                next_action=self._normalize_action(next_action) if next_action is not None else None,
                summary=str(decision.get("summary") or ""),
            )
        return CodexEvaluationDecision()

    def _maybe_pause_for_approval(
        self,
        task: CodexTask,
        action_index: int,
        action: CodexAction,
        session: AssistantSession,
    ) -> tuple[ApprovalRequest, ResumeToken] | None:
        if bool(action.metadata.get("_approval_granted")):
            return None
        if action.risk_class not in {"high", "destructive"}:
            return None
        return self._store_approval(task, action_index, action, session, reason=f"action '{action.kind}' requires confirmation", risk_class=action.risk_class)

    def _pause_for_result_approval(
        self,
        task: CodexTask,
        action_index: int,
        action: CodexAction,
        result: CodexActionResult,
        session: AssistantSession,
    ) -> tuple[ApprovalRequest, ResumeToken]:
        return self._store_approval(
            task,
            action_index,
            action,
            session,
            reason=result.approval_reason or f"action '{action.kind}' requires confirmation",
            risk_class=result.risk_class or action.risk_class or "high",
        )

    def _store_approval(
        self,
        task: CodexTask,
        action_index: int,
        action: CodexAction,
        session: AssistantSession,
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
        self._pending_approvals[token.token_id] = _PendingCodexApproval(
            session=session,
            task=task,
            action_index=action_index,
            request=request,
        )
        action.status = "awaiting_approval"
        return request, token

    def _execute_action(self, action: CodexAction, session: AssistantSession) -> CodexActionResult:
        executor = self.context.services.get("action_executor")
        if callable(executor):
            result = executor(action, session, self.context)
            return self._normalize_result(result)
        if action.kind == "call_tool":
            return self._execute_tool_action(action)
        if action.kind == "apply_patch":
            return self._execute_tool_action(action)
        if action.kind == "create_path":
            return self._execute_tool_action(action)
        if action.kind == "edit_text":
            return self._execute_tool_action(action)
        if action.kind == "move_path":
            return self._execute_tool_action(action)
        if action.kind == "delete_path":
            return self._execute_tool_action(action)
        if action.kind == "run_verification":
            return self._execute_verification_action(action)
        if action.kind == "respond":
            if bool(action.metadata.get("direct_output")):
                return CodexActionResult(status="completed", final_output=action.instruction)
            diagnostics: dict[str, str | None] = {"source": "fallback", "reason": "unknown"}
            final_output = "".join(stream_conversation_reply(action.instruction, self.context, session, diagnostics=diagnostics))
            return CodexActionResult(
                status="completed",
                final_output=final_output,
                metadata={"conversation": diagnostics},
            )
        return CodexActionResult(
            status="failed",
            final_output=f"unsupported action kind: {action.kind}",
        )

    def _execute_tool_action(self, action: CodexAction) -> CodexActionResult:
        tool_name = str(action.metadata.get("tool_name") or "").strip()
        arguments = dict(action.metadata.get("arguments") or {})
        if not tool_name:
            return CodexActionResult(status="failed", final_output="missing tool_name")
        tool = self.context.application_context.tools.require(tool_name)
        result = execute_tool_call(
            tool,
            ToolCall(tool_name=tool_name, arguments=arguments),
            task=action,
            context=self.context,
        )
        if not result.success:
            if result.exception is not None:
                raise result.exception
            return CodexActionResult(status="failed", final_output=str(result.error or "tool execution failed"))
        output = result.output
        if isinstance(output, dict):
            final_output = str(output.get("text") or output.get("content") or output.get("stdout") or output)
            artifacts = []
            if action.kind in {"apply_patch", "create_path", "edit_text", "move_path", "delete_path"}:
                artifacts.append(
                    {
                        "artifact_type": "change_summary",
                        "title": tool_name,
                        "content": str(output.get("after_text") or output.get("text") or final_output),
                        "metadata": {"path": str(output.get("path") or "")},
                    }
                )
            return CodexActionResult(
                status="completed",
                final_output=final_output,
                artifacts=artifacts,
                metadata={"tool_output": output},
            )
        return CodexActionResult(status="completed", final_output=str(output or ""))

    def _execute_verification_action(self, action: CodexAction) -> CodexActionResult:
        command = str(action.metadata.get("command") or action.instruction or "").strip()
        tool = self.context.application_context.tools.require("run_shell_command")
        result = execute_tool_call(
            tool,
            ToolCall(tool_name="run_shell_command", arguments={"command": command}),
            task=action,
            context=self.context,
        )
        if not result.success:
            return CodexActionResult(status="failed", final_output=str(result.error or "verification failed"))
        output = dict(result.output or {})
        success = bool(output.get("success"))
        summary = str(output.get("text") or output.get("stdout") or output.get("stderr") or "")
        return CodexActionResult(
            status="completed" if success else "failed",
            final_output=summary,
            artifacts=[
                {
                    "artifact_type": "verification_log",
                    "title": command,
                    "content": summary,
                    "metadata": {"command": command, "success": success},
                }
            ],
            metadata={"verification": {"success": success, "summary": summary, "command": command}},
        )

    def _normalize_result(self, result: Any) -> CodexActionResult:
        if isinstance(result, CodexActionResult):
            return result
        if isinstance(result, dict):
            return CodexActionResult(
                status=str(result.get("status") or "completed"),
                final_output=str(result.get("final_output") or result.get("text") or ""),
                artifacts=list(result.get("artifacts") or []),
                artifact_ids=list(result.get("artifact_ids") or []),
                needs_approval=bool(result.get("needs_approval")),
                approval_reason=str(result.get("approval_reason") or ""),
                risk_class=str(result.get("risk_class") or ""),
                metadata=dict(result.get("metadata") or {}),
            )
        return CodexActionResult(status="completed", final_output=str(result or ""))

    def _persist_artifacts(
        self,
        task: CodexTask,
        action: CodexAction,
        artifacts: list[dict[str, Any]],
        run_id: str,
    ) -> list[str]:
        store = self.context.services.get("artifact_store")
        if store is None or not hasattr(store, "add"):
            return []
        artifact_ids: list[str] = []
        for artifact in artifacts:
            record = store.add(
                str(artifact.get("artifact_type") or "action_output"),
                title=str(artifact.get("title") or action.kind),
                content=str(artifact.get("content") or ""),
                metadata={
                    "task_id": task.task_id,
                    "run_id": run_id,
                    "action_kind": action.kind,
                    **dict(artifact.get("metadata") or {}),
                },
            )
            artifact_ids.append(record.artifact_id)
        return artifact_ids

    def _build_verification(self, task: CodexTask, final_output: str) -> VerificationResult:
        last_verification = None
        for action in reversed(task.actions):
            verification = action.metadata.get("verification_result")
            if isinstance(verification, VerificationResult):
                last_verification = verification
                break
        verifier = self.context.services.get("verifier")
        if callable(verifier):
            result = verifier(task, final_output, self.context)
            if isinstance(result, VerificationResult):
                return result
            if isinstance(result, dict):
                return VerificationResult(
                    success=bool(result.get("success")),
                    summary=str(result.get("summary") or ""),
                    evidence=list(result.get("evidence") or []),
                )
        if last_verification is not None:
            return last_verification
        return VerificationResult(success=True, summary="Task completed.", evidence=[final_output] if final_output else [])
