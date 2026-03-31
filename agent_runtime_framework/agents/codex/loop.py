from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.conversation import stream_conversation_reply
from agent_runtime_framework.assistant.approval import ApprovalRequest, ResumeToken
from agent_runtime_framework.assistant.session import AssistantSession
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.agents.codex.models import (
    CodexAction,
    CodexActionResult,
    CodexEvaluationDecision,
    CodexTask,
    TaskIntent,
    VerificationResult,
)
from agent_runtime_framework.agents.codex.answer_synthesizer import build_synthesized_response_action
from agent_runtime_framework.agents.codex.memory_extractor import extract_memory_items
from agent_runtime_framework.agents.codex.memory_policy import decide_memory_write
from agent_runtime_framework.agents.codex.evidence_manager import record_action_evidence
from agent_runtime_framework.agents.codex.memory import update_task_memory
from agent_runtime_framework.agents.codex.planner import plan_next_codex_action
from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.profiles import classify_task_profile
from agent_runtime_framework.agents.codex.run_context import update_loaded_instructions
from agent_runtime_framework.agents.codex.runtime import CodexSessionRuntime
from agent_runtime_framework.agents.codex.semantics import resolve_task_intent
from agent_runtime_framework.agents.codex.state import build_initial_task_state, sync_task_state_from_memory
from agent_runtime_framework.agents.codex.task_plans import (
    advance_task_plan,
    attach_action_to_plan,
    build_task_plan,
    has_pending_plan_task,
    plan_next_task_action,
    sync_task_plan,
)
from agent_runtime_framework.memory import MemoryRecord
from agent_runtime_framework.resources import ResourceRef, describe_resource_semantics
from agent_runtime_framework.tools import ToolCall, execute_tool_call

_PENDING_CLARIFICATION_KEY = "codex:pending_clarification"


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


def _merge_clarification_goal(goal: str, clarification: str) -> str:
    base = goal.strip()
    detail = clarification.strip()
    if not base:
        return detail
    if not detail:
        return base
    return f"{base}\nUser clarification: {detail}"


class CodexAgentLoop:
    def __init__(self, context: CodexContext) -> None:
        self.context = context
        self._pending_approvals: dict[str, _PendingCodexApproval] = {}
        self._pending_clarifications: dict[str, CodexTask] = {}

    def run(self, user_input: str) -> CodexAgentLoopResult:
        session = self._require_session()
        session.add_turn("user", user_input)
        task = self._build_task(user_input, session)
        self._runtime().on_task_started(task)
        result = self._execute_task(task, session, start_index=0)
        if result.status in {"completed", "needs_clarification"}:
            session.add_turn("assistant", result.final_output)
        return result

    def has_pending_clarification(self, session: AssistantSession | None = None) -> bool:
        active_session = session or self.context.session
        if active_session is not None and active_session.session_id in self._pending_clarifications:
            return True
        return self._pending_clarification_payload() is not None

    def resume(self, token: ResumeToken, *, approved: bool) -> CodexAgentLoopResult:
        pending = self._pending_approvals.pop(token.token_id, None)
        if pending is None or not approved:
            task = pending.task if pending is not None else self._new_task("", session=self._require_session())
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

    def _runtime(self) -> CodexSessionRuntime:
        runtime = self.context.services.get("session_runtime")
        if isinstance(runtime, CodexSessionRuntime):
            self.context.application_context.services["tool_runtime"] = runtime
            return runtime
        runtime = CodexSessionRuntime()
        self.context.services["session_runtime"] = runtime
        self.context.application_context.services["tool_runtime"] = runtime
        return runtime

    def _workspace_root(self) -> Path | None:
        root_value = self.context.application_context.config.get("default_directory")
        return Path(str(root_value)) if root_value else None

    def _new_task(self, goal: str, *, session: AssistantSession) -> CodexTask:
        intent = resolve_task_intent(goal, self.context, session=session)
        task = CodexTask(goal=goal, actions=[], task_profile=intent.task_kind, intent=intent, state=build_initial_task_state(intent))
        sync_task_state_from_memory(task)
        return task

    def _build_task(self, user_input: str, session: AssistantSession) -> CodexTask:
        pending = self._pending_clarifications.pop(session.session_id, None)
        if pending is None:
            pending = self._restore_persisted_pending_clarification()
        if pending is not None:
            self._clear_persisted_pending_clarification()
            merged_goal = _merge_clarification_goal(pending.goal, user_input)
            intent = resolve_task_intent(merged_goal, self.context, session=session)
            task = CodexTask(
                goal=merged_goal,
                actions=[],
                task_profile=intent.task_kind or pending.task_profile,
                intent=intent,
                state=build_initial_task_state(intent),
                runtime_persona=str(getattr(pending, "runtime_persona", "") or ""),
            )
            task.memory = pending.memory
            task.runtime_persona = resolve_runtime_persona(self.context, task=task, user_input=task.goal).name
            session.active_persona = task.runtime_persona
            task.plan = build_task_plan(task, self.context)
            sync_task_state_from_memory(task)
            return task
        intent = resolve_task_intent(user_input, self.context, session=session)
        task_profile = intent.task_kind or classify_task_profile(user_input, self.context, session=session)
        planner = self.context.services.get("action_planner")
        if callable(planner):
            planned = planner(user_input, session, self.context)
            if isinstance(planned, CodexTask):
                planned.task_profile = task_profile
                planned.intent = intent
                planned.state = build_initial_task_state(intent)
                planned.runtime_persona = resolve_runtime_persona(self.context, task=planned, user_input=user_input).name
                session.active_persona = planned.runtime_persona
                for action in planned.actions:
                    self._ensure_action_subgoal(action)
                    if action.kind == "respond" and "direct_output" not in action.metadata:
                        action.metadata["direct_output"] = True
                sync_task_state_from_memory(planned)
                return planned
            if isinstance(planned, list):
                actions = [self._normalize_action(item) for item in planned]
                for action in actions:
                    self._ensure_action_subgoal(action)
                    if action.kind == "respond" and "direct_output" not in action.metadata:
                        action.metadata["direct_output"] = True
                task = CodexTask(goal=user_input, actions=actions, task_profile=task_profile, intent=intent, state=build_initial_task_state(intent))
                task.runtime_persona = resolve_runtime_persona(self.context, task=task, user_input=user_input).name
                session.active_persona = task.runtime_persona
                sync_task_state_from_memory(task)
                return task
        task = self._new_task(user_input, session=session)
        task.task_profile = task_profile
        task.runtime_persona = resolve_runtime_persona(self.context, task=task, user_input=user_input).name
        session.active_persona = task.runtime_persona
        task.plan = build_task_plan(task, self.context)
        sync_task_state_from_memory(task)
        return task

    def _normalize_action(self, action: Any) -> CodexAction:
        if isinstance(action, CodexAction):
            self._ensure_action_subgoal(action)
            return action
        if isinstance(action, dict):
            normalized = CodexAction(
                kind=str(action.get("kind") or ""),
                instruction=str(action.get("instruction") or ""),
                subgoal=str(action.get("subgoal") or "execute_step"),
                risk_class=str(action.get("risk_class") or "low"),
                metadata=dict(action.get("metadata") or {}),
            )
            self._ensure_action_subgoal(normalized)
            return normalized
        raise TypeError(f"unsupported action type: {type(action)!r}")

    def _ensure_action_subgoal(self, action: CodexAction) -> None:
        if action.subgoal != "execute_step":
            return
        if action.kind == "respond":
            action.subgoal = "synthesize_answer"
        elif action.kind == "run_verification":
            action.subgoal = "verify_changes"
        elif action.kind in {"call_tool"}:
            action.subgoal = "gather_evidence"
        elif action.kind in {"apply_patch", "create_path", "edit_text", "move_path", "delete_path"}:
            action.subgoal = "modify_workspace"

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
        step_budget = resolve_runtime_persona(self.context, task=task, user_input=task.goal).default_step_budget
        while True:
            completed_actions = sum(1 for item in task.actions if item.status == "completed")
            if completed_actions >= step_budget and self._next_action_index(task, start_index=action_index) is not None:
                task.status = "failed"
                return CodexAgentLoopResult(
                    status="failed",
                    final_output=f"step budget exceeded for persona '{task.runtime_persona or 'general'}'",
                    task=task,
                    action_kind="step_budget_exceeded",
                    run_id=run_id,
                )
            next_index = self._next_action_index(task, start_index=action_index)
            if next_index is None:
                forced_summary = self._build_completion_guard_action(task)
                if forced_summary is not None:
                    task.actions.append(forced_summary)
                    next_index = len(task.actions) - 1
                    attach_action_to_plan(task, forced_summary, next_index)
                else:
                    planned = self._plan_next_action(task, session)
                    if planned is None:
                        break
                    if completed_actions + 1 > step_budget:
                        task.status = "failed"
                        return CodexAgentLoopResult(
                            status="failed",
                            final_output=f"step budget exceeded for persona '{task.runtime_persona or 'general'}'",
                            task=task,
                            action_kind="step_budget_exceeded",
                            run_id=run_id,
                        )
                    task.actions.append(planned)
                    next_index = len(task.actions) - 1
                    attach_action_to_plan(task, planned, next_index)
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
            try:
                result = self._execute_action(action, session)
            except AppError as exc:
                if self._maybe_retry_action_after_exception(action, exc):
                    continue
                if not exc.retriable:
                    raise
                result = self._result_from_app_error(exc)
            if self._maybe_retry_action_after_result(action, result):
                continue
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
            update_task_memory(task, action, result)
            record_action_evidence(task, action, result)
            advance_task_plan(task, action, result, self.context)
            sync_task_plan(task)
            sync_task_state_from_memory(task)
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
            self._runtime().record_action(task, action)
            if bool(action.metadata.get("clarification_required")):
                task.status = "needs_clarification"
                self._pending_clarifications[session.session_id] = task
                self._store_persisted_pending_clarification(task, result.final_output)
                return CodexAgentLoopResult(
                    status="needs_clarification",
                    final_output=result.final_output,
                    task=task,
                    action_kind="clarify_target",
                    run_id=run_id,
                )
            if result.status != "completed":
                if has_pending_plan_task(task):
                    action_index = next_index + 1
                    continue
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
        self._clear_persisted_pending_clarification()
        task.summary = self._runtime().build_task_summary(task)
        task.verification = self._build_verification(task, final_output)
        self._remember_completed_task(task, final_output)
        return CodexAgentLoopResult(
            status="completed",
            final_output=final_output,
            task=task,
            action_kind=last_kind,
            run_id=run_id,
        )

    def _build_completion_guard_action(self, task: CodexTask) -> CodexAction | None:
        if has_pending_plan_task(task):
            return None
        if not self._task_requires_user_visible_summary(task):
            return None
        completed = [action for action in task.actions if action.status == "completed"]
        if not completed:
            return None
        last_action = completed[-1]
        if last_action.kind == "respond":
            return None
        return build_synthesized_response_action(task, source="completion_guard", extra_metadata={"from_completion_guard": True})

    def _task_requires_user_visible_summary(self, task: CodexTask) -> bool:
        profile = str(getattr(task, "task_profile", "") or "")
        if profile in {"change_and_verify", "multi_file_change", "debug_and_fix", "test_and_verify"}:
            return True
        return any(action.subgoal in {"modify_workspace", "verify_changes"} for action in task.actions if action.status == "completed")

    def _build_delivery_summary(
        self,
        task: CodexTask,
        last_action: CodexAction,
        modified_paths: list[str],
        last_observation: str,
    ) -> str:
        lines: list[str] = []
        lines.append(f"Completed the requested update: {self._describe_change_outcome(last_action, last_observation)}.")
        if modified_paths:
            lines.append(f"Files changed: {', '.join(modified_paths[:4])}.")
        else:
            lines.append("Files changed: not explicitly recorded.")
        verification_status, verification_detail = self._describe_verification_outcome(task, last_action)
        detail_suffix = f" ({verification_detail})" if verification_detail else ""
        lines.append(f"Verification: {verification_status}.{detail_suffix}")
        return " ".join(line.strip() for line in lines if line.strip()).strip()

    def _describe_change_outcome(self, action: CodexAction, last_observation: str) -> str:
        tool_name = str(action.metadata.get("tool_name") or "").strip()
        if tool_name == "create_workspace_path":
            return "created the requested file or directory"
        if tool_name in {"edit_workspace_text", "apply_text_patch"}:
            return "updated the requested file content"
        if tool_name == "move_workspace_path":
            return "moved the requested path"
        if tool_name == "delete_workspace_path":
            return "deleted the requested path"
        compact = " ".join(last_observation.split()).strip()
        return compact[:160].rstrip() or "completed the requested workspace change"

    def _describe_verification_outcome(self, task: CodexTask, last_action: CodexAction) -> tuple[str, str]:
        verification = getattr(task, "verification", None)
        if verification is not None:
            summary = self._compact_summary(str(getattr(verification, "summary", "") or ""))
            return ("passed" if bool(getattr(verification, "success", False)) else "failed", summary)
        payload_value = last_action.metadata.get("verification_result")
        if hasattr(payload_value, "success") and hasattr(payload_value, "summary"):
            summary = self._compact_summary(str(getattr(payload_value, "summary", "") or ""))
            return ("passed" if bool(getattr(payload_value, "success", False)) else "failed", summary)
        payload = dict(payload_value or {})
        if payload:
            summary = self._compact_summary(str(payload.get("summary") or ""))
            return ("passed" if bool(payload.get("success")) else "failed", summary)
        if task.memory.pending_verifications:
            return ("pending", "")
        return ("not run", "")

    def _compact_summary(self, text: str, *, limit: int = 140) -> str:
        compact = " ".join(str(text or "").split()).strip()
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    def _pending_clarification_payload(self) -> dict[str, Any] | None:
        index_memory = getattr(self.context.application_context, "index_memory", None)
        get = getattr(index_memory, "get", None)
        if not callable(get):
            return None
        payload = get(_PENDING_CLARIFICATION_KEY)
        return dict(payload) if isinstance(payload, dict) else None

    def _restore_persisted_pending_clarification(self) -> CodexTask | None:
        payload = self._pending_clarification_payload()
        if payload is None:
            return None
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            return None
        task = CodexTask(
            goal=goal,
            actions=[],
            task_profile=str(payload.get("task_profile") or "chat").strip() or "chat",
            runtime_persona=str(payload.get("runtime_persona") or "").strip(),
        )
        memory_payload = dict(payload.get("memory") or {})
        for field_name, value in memory_payload.items():
            if hasattr(task.memory, field_name) and isinstance(value, list):
                setattr(task.memory, field_name, [item for item in value if isinstance(item, (str, dict))])
        task.plan = build_task_plan(task, self.context)
        return task

    def _store_persisted_pending_clarification(self, task: CodexTask, message: str) -> None:
        index_memory = getattr(self.context.application_context, "index_memory", None)
        put = getattr(index_memory, "put", None)
        if not callable(put):
            return
        put(
            _PENDING_CLARIFICATION_KEY,
            {
                "goal": task.goal,
                "task_profile": task.task_profile,
                "runtime_persona": task.runtime_persona,
                "message": message,
                "memory": asdict(task.memory),
            },
        )

    def _clear_persisted_pending_clarification(self) -> None:
        index_memory = getattr(self.context.application_context, "index_memory", None)
        put = getattr(index_memory, "put", None)
        if callable(put):
            put(_PENDING_CLARIFICATION_KEY, None)

    def _next_action_index(self, task: CodexTask, *, start_index: int) -> int | None:
        for index in range(max(0, start_index), len(task.actions)):
            if task.actions[index].status == "pending":
                return index
        return None

    def _plan_next_action(self, task: CodexTask, session: AssistantSession) -> CodexAction | None:
        planned_from_task_plan = plan_next_task_action(task)
        if planned_from_task_plan is not None:
            return planned_from_task_plan
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

    def _retry_limit_for_action(self, action: CodexAction) -> int:
        configured = action.metadata.get("retry_limit")
        if configured is None:
            configured = self.context.application_context.config.get("codex_retry_limit", 1)
        try:
            return max(0, int(configured))
        except (TypeError, ValueError):
            return 1

    def _action_allows_safe_retry(self, action: CodexAction) -> bool:
        if bool(action.metadata.get("allow_retry")):
            return True
        if action.risk_class in {"high", "destructive"}:
            return False
        if action.kind in {"respond", "apply_patch", "create_path", "edit_text", "move_path", "delete_path"}:
            return False
        return action.subgoal in {"gather_evidence", "verify_changes"} or action.kind in {"call_tool", "run_verification", "locate_target"}

    def _record_retry_attempt(self, action: CodexAction, *, code: str = "", reason: str = "") -> bool:
        if not self._action_allows_safe_retry(action):
            return False
        retry_count = int(action.metadata.get("_retry_count") or 0)
        retry_limit = self._retry_limit_for_action(action)
        if retry_count >= retry_limit:
            return False
        action.metadata["_retry_count"] = retry_count + 1
        if code:
            action.metadata["_last_retry_code"] = code
        if reason:
            action.metadata["_last_retry_reason"] = reason
        return True

    def _maybe_retry_action_after_exception(self, action: CodexAction, exc: AppError) -> bool:
        if not exc.retriable:
            return False
        return self._record_retry_attempt(action, code=exc.code, reason=exc.message)

    def _maybe_retry_action_after_result(self, action: CodexAction, result: CodexActionResult) -> bool:
        if result.status != "failed":
            return False
        error_payload = dict(result.metadata.get("error") or {})
        if not bool(error_payload.get("retriable")):
            return False
        return self._record_retry_attempt(
            action,
            code=str(error_payload.get("code") or ""),
            reason=str(result.final_output or error_payload.get("message") or ""),
        )

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
        if action.kind == "locate_target":
            return self._execute_locate_target_action(action)
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

    def _execute_locate_target_action(self, action: CodexAction) -> CodexActionResult:
        target_hint = str(action.metadata.get("target_hint") or "").strip()
        root = Path(self.context.application_context.config.get("default_directory") or "").expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(root)
        resolved = root
        if target_hint:
            candidate = Path(target_hint).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve(strict=False)
            if not resolved.exists():
                repository = self.context.application_context.resource_repository
                matches = repository.find_by_name(ResourceRef.for_path(root), target_hint)
                if matches:
                    resolved = Path(matches[0].location).expanduser().resolve()
        if not (resolved == root or root in resolved.parents):
            raise ValueError(f"path is outside allowed roots: {resolved}")
        label = str(resolved.relative_to(root)) if resolved != root else root.name
        summary = f"Located target: {label}"
        update_loaded_instructions(self.context, str(resolved))
        semantics = describe_resource_semantics(
            ResourceRef.for_path(resolved),
            self.context.application_context.resource_repository,
        )
        return CodexActionResult(
            status="completed",
            final_output=summary,
            metadata={
                "tool_output": {
                    "path": str(resolved),
                    "resolved_path": str(resolved),
                    "summary": summary,
                    "text": summary,
                    "is_directory": resolved.is_dir(),
                    "resource_kind": semantics.resource_kind,
                    "is_container": semantics.is_container,
                    "allowed_actions": list(semantics.allowed_actions),
                }
            },
        )

    def _execute_tool_action(self, action: CodexAction) -> CodexActionResult:
        tool_name = str(action.metadata.get("tool_name") or "").strip()
        arguments = dict(action.metadata.get("arguments") or {})
        if not tool_name:
            return CodexActionResult(status="failed", final_output="missing tool_name")
        tool = self.context.application_context.tools.get(tool_name)
        if tool is None:
            repaired = self.context.application_context.tools.find_case_insensitive(tool_name)
            if repaired is not None:
                action.metadata["requested_tool_name"] = tool_name
                action.metadata["tool_name"] = repaired.name
                tool_name = repaired.name
                tool = repaired
            else:
                suggestions = self.context.application_context.tools.suggest(tool_name)
                return CodexActionResult(
                    status="failed",
                    final_output=f"unknown tool: {tool_name}",
                    metadata={
                        "error": {
                            "code": "TOOL_NOT_FOUND",
                            "message": f"unknown tool: {tool_name}",
                            "available_tools": self.context.application_context.tools.names(),
                            "suggestions": suggestions,
                            "retriable": True,
                        }
                    },
                )
        access_result = self._enforce_persona_tool_access(action, tool, session=self.context.session)
        if access_result is not None:
            return access_result
        try:
            result = execute_tool_call(
                tool,
                ToolCall(tool_name=tool_name, arguments=arguments),
                task=action,
                context=self.context,
            )
        except IsADirectoryError:
            recovered = self._recover_directory_tool_action(action, tool_name, arguments)
            if recovered is not None:
                return recovered
            raise
        if not result.success:
            if isinstance(result.exception, IsADirectoryError):
                recovered = self._recover_directory_tool_action(action, tool_name, arguments)
                if recovered is not None:
                    return recovered
            if result.exception is not None:
                raise result.exception
            return CodexActionResult(
                status="failed",
                final_output=str(result.error or "tool execution failed"),
                metadata={"error": dict(result.metadata.get("error") or {})},
            )
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

    def _recover_directory_tool_action(
        self,
        action: CodexAction,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CodexActionResult | None:
        if tool_name not in {"read_workspace_text", "summarize_workspace_text"}:
            return None
        recovery_tool_name = "inspect_workspace_path" if "inspect_workspace_path" in self.context.application_context.tools.names() else ""
        if not recovery_tool_name and "list_workspace_directory" in self.context.application_context.tools.names():
            recovery_tool_name = "list_workspace_directory"
        if not recovery_tool_name:
            return None

        recovery_arguments = {
            "path": str(arguments.get("path") or ""),
            "use_last_focus": bool(arguments.get("use_last_focus")),
            "use_default_directory": bool(arguments.get("use_default_directory")),
        }
        recovery_tool = self.context.application_context.tools.require(recovery_tool_name)
        result = execute_tool_call(
            recovery_tool,
            ToolCall(tool_name=recovery_tool_name, arguments=recovery_arguments),
            task=action,
            context=self.context,
        )
        if not result.success:
            if result.exception is not None:
                raise result.exception
            return CodexActionResult(status="failed", final_output=str(result.error or "tool execution failed"))

        action.metadata["requested_tool_name"] = tool_name
        action.metadata["tool_name"] = recovery_tool_name
        action.metadata["recovered_from_directory"] = True
        action.metadata["directory_recovery_source"] = tool_name

        output = dict(result.output or {})
        final_output = str(output.get("text") or output.get("content") or output.get("stdout") or output)
        metadata = {"tool_output": output, "directory_recovery": {"from_tool": tool_name, "to_tool": recovery_tool_name}}
        return CodexActionResult(status="completed", final_output=final_output, metadata=metadata)

    def _execute_verification_action(self, action: CodexAction) -> CodexActionResult:
        command = str(action.metadata.get("command") or action.instruction or "").strip()
        tool = self.context.application_context.tools.require("run_shell_command")
        access_result = self._enforce_persona_tool_access(action, tool, session=self.context.session)
        if access_result is not None:
            return access_result
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

    def _enforce_persona_tool_access(self, action: CodexAction, tool: Any, *, session: AssistantSession | None) -> CodexActionResult | None:
        from agent_runtime_framework.agents.codex.personas import tool_access_for_persona

        persona = resolve_runtime_persona(self.context, task=None, user_input=action.instruction)
        access = tool_access_for_persona(persona, tool)
        if session is not None:
            session.active_persona = persona.name
        action.metadata["runtime_persona"] = persona.name
        action.metadata["persona_tool_access"] = access
        tool_name = str(getattr(tool, "name", "") or action.metadata.get("tool_name") or "")
        if access == "deny":
            return CodexActionResult(
                status="failed",
                final_output=f"persona '{persona.name}' does not allow tool '{tool_name}'",
                metadata={
                    "error": {
                        "code": "PERSONA_TOOL_DENIED",
                        "message": f"persona '{persona.name}' denied tool '{tool_name}'",
                        "retriable": False,
                    }
                },
            )
        if access == "ask" and not bool(action.metadata.get("_approval_granted")):
            return CodexActionResult(
                status="pending",
                final_output=f"persona '{persona.name}' requires confirmation for tool '{tool_name}'",
                needs_approval=True,
                approval_reason=f"persona '{persona.name}' requires confirmation for tool '{tool_name}'",
                risk_class=action.risk_class or "high",
                metadata={"persona_tool_access": access, "runtime_persona": persona.name},
            )
        return None

    def _result_from_app_error(self, error: AppError) -> CodexActionResult:
        payload = error.as_dict()
        return CodexActionResult(
            status="failed",
            final_output=str(error.message or error.code or "action failed"),
            metadata={"error": payload},
        )

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

    def _remember_completed_task(self, task: CodexTask, final_output: str) -> None:
        index_memory = getattr(self.context.application_context, "index_memory", None)
        remember = getattr(index_memory, "remember", None)
        if not callable(remember):
            return
        target_path = self._completed_task_target_path(task)
        relative_path = self._relative_workspace_path(target_path) if target_path else ""
        for item in extract_memory_items(task, final_output=final_output):
            path = relative_path if item.path in {"", "."} else self._relative_workspace_path(item.path)
            item.path = path or relative_path
            decision = decide_memory_write(item)
            if not decision.allow_write:
                continue
            remember(
                MemoryRecord(
                    key=item.memory_id,
                    text=f"{task.goal} {item.text}".strip(),
                    kind="entity_binding" if decision.target_layer == "entity" else ("task_conclusion" if item.record_kind == "summary" else "workspace_fact"),
                    metadata={
                        **item.as_metadata(),
                        "path": item.path,
                        "task_profile": task.task_profile,
                        "goal": task.goal,
                        "layer": decision.target_layer,
                        "confidence": decision.confidence,
                        "retrievable_for_resolution": decision.retrievable_for_resolution,
                    },
                )
            )
        for index, claim in enumerate(task.memory.typed_claims[:5]):
            detail = " ".join(
                str(claim.get(field) or "").strip()
                for field in ("subject", "detail", "kind")
                if str(claim.get(field) or "").strip()
            )
            if not detail:
                continue
            remember(
                MemoryRecord(
                    key=f"task:{task.task_id}:typed:{index}",
                    text=f"{task.goal} {detail}".strip(),
                    kind="workspace_fact",
                    metadata={
                        "path": relative_path,
                        "task_profile": task.task_profile,
                        "claim_kind": str(claim.get("kind") or ""),
                        "layer": "daily",
                        "record_kind": "observation",
                        "confidence": 0.5,
                        "retrievable_for_resolution": bool(claim.get("kind") == "role" and relative_path and relative_path != "."),
                    },
                )
            )

    def _completed_task_target_path(self, task: CodexTask) -> str:
        plan = task.plan
        if plan is not None:
            if plan.target_semantics is not None and plan.target_semantics.path:
                return str(plan.target_semantics.path)
            resolved_path = str(plan.metadata.get("resolved_path") or "").strip()
            if resolved_path:
                return resolved_path
        if task.memory.read_paths:
            return str(task.memory.read_paths[-1])
        snapshot = self.context.application_context.session_memory.snapshot()
        if snapshot.focused_resources:
            return str(snapshot.focused_resources[0].location)
        return ""

    def _relative_workspace_path(self, path: str) -> str:
        if not path:
            return ""
        roots = getattr(self.context.application_context.resource_repository, "allowed_roots", [])
        if not roots:
            return path.strip()
        root = Path(roots[0]).expanduser().resolve()
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve()
        except FileNotFoundError:
            resolved = candidate.resolve(strict=False)
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            return path.strip()
        return relative or "."
