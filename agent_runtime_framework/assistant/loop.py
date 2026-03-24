from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.approval import ApprovalManager, ApprovalRequest, ResumeToken
from agent_runtime_framework.assistant.checkpoints import CheckpointRecord, CheckpointStore
from agent_runtime_framework.assistant.capabilities import CapabilityRegistry
from agent_runtime_framework.assistant.conversation import route_default_capability
from agent_runtime_framework.assistant.session import AssistantSession, ExecutionPlan, PlannedAction
from agent_runtime_framework.assistant.skills import SkillRegistry
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.graph import ExecutionContext, FunctionNode, RuleRouter, StateGraph
from agent_runtime_framework.models import resolve_model_runtime
from agent_runtime_framework.runtime import parse_structured_output


@dataclass(slots=True)
class AssistantContext:
    application_context: ApplicationContext
    capabilities: CapabilityRegistry
    skills: SkillRegistry
    services: dict[str, Any] = field(default_factory=dict)
    session: AssistantSession | None = None


@dataclass(slots=True)
class AgentLoopResult:
    status: str
    final_answer: str
    capability_name: str
    execution_trace: list[dict[str, Any]] = field(default_factory=list)
    approval_request: ApprovalRequest | None = None
    resume_token: ResumeToken | None = None
    run_id: str = ""
    plan_id: str | None = None
    failed_step_index: int | None = None


@dataclass(slots=True)
class _LoopRunState:
    run_id: str
    session: AssistantSession
    user_input: str
    loop_input: str
    current_node: str | None = None
    step_count: int = 0
    execution_trace: list[str] = field(default_factory=list)
    routing_history: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    status: str = "running"
    done: bool = False
    last_node: str | None = None
    last_observation: str | None = None
    termination_reason: str | None = None
    plan: ExecutionPlan | None = None
    plan_start_index: int = 0
    skip_approval_for: set[int] = field(default_factory=set)
    review_decision: str = "stop"
    last_result: AgentLoopResult | None = None

    def add_trace(self, node_name: str) -> None:
        self.execution_trace.append(node_name)

    def add_note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def record_route(self, source: str, next_node: str, reason: str) -> None:
        self.routing_history.append(
            {
                "source": source,
                "next_node": next_node,
                "reason": reason,
            }
        )


class AgentLoop:
    def __init__(self, context: AssistantContext) -> None:
        self.context = context
        self._compiled_graph = self._build_graph().compile()

    def run(self, user_input: str) -> AgentLoopResult:
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        session.add_turn("user", user_input)
        return self._run_with_graph(
            user_input,
            session,
            plan=None,
            plan_start_index=0,
            skip_approval_for=set(),
        )

    def resume(self, token: ResumeToken, *, approved: bool) -> AgentLoopResult:
        approval_manager = self._approval_manager()
        if approval_manager is None:
            raise RuntimeError("approval manager is not configured")
        pending = approval_manager.resolve(token, approved)
        if pending is None:
            return AgentLoopResult(
                status="cancelled",
                final_answer="approval was rejected or expired",
                capability_name="",
                run_id=str(uuid4()),
                plan_id=token.plan_id,
            )
        self.context.session = pending.session
        pending.session.mark_step_confirmed(pending.plan.plan_id, pending.step_index)
        return self._run_with_graph(
            pending.plan.goal,
            pending.session,
            plan=pending.plan,
            plan_start_index=pending.step_index,
            skip_approval_for={pending.step_index},
        )

    def replay(self, run_id: str) -> AgentLoopResult:
        store = self.context.services.get("checkpoint_store")
        if store is None or not hasattr(store, "replay_input"):
            raise RuntimeError("checkpoint store with replay support is not configured")
        goal = str(store.replay_input(run_id) or "").strip()
        if not goal:
            raise RuntimeError(f"run '{run_id}' does not have replayable input")
        return self.run(goal)

    def _build_graph(self) -> StateGraph[_LoopRunState]:
        graph = StateGraph[_LoopRunState]()
        graph.add_node("plan", FunctionNode(self._node_plan))
        graph.add_node("execute", FunctionNode(self._node_execute))
        graph.add_node("review", FunctionNode(self._node_review))
        graph.add_node("finish", FunctionNode(self._node_finish))
        graph.add_edge("plan", "execute")
        graph.add_conditional_edges(
            "execute",
            RuleRouter(self._route_after_execute),
            {"review": "review", "finish": "finish"},
        )
        graph.add_conditional_edges(
            "review",
            RuleRouter(self._route_after_review),
            {"plan": "plan", "finish": "finish"},
        )
        graph.set_entry_point("plan")
        graph.set_finish_point("finish")
        return graph

    def _run_with_graph(
        self,
        user_input: str,
        session: AssistantSession,
        *,
        plan: ExecutionPlan | None,
        plan_start_index: int,
        skip_approval_for: set[int],
    ) -> AgentLoopResult:
        run_id = str(uuid4())
        state = _LoopRunState(
            run_id=run_id,
            session=session,
            user_input=user_input,
            loop_input=user_input,
            plan=plan,
            plan_start_index=plan_start_index,
            skip_approval_for=set(skip_approval_for),
        )
        self._compiled_graph.run(state, ExecutionContext(services={"agent_loop": self}))
        result = state.last_result or AgentLoopResult(
            status=state.status if state.status != "running" else "cancelled",
            final_answer=state.last_observation or "",
            capability_name="",
            run_id=run_id,
            plan_id=state.plan.plan_id if state.plan is not None else None,
        )
        result.run_id = run_id
        if result.status == "completed":
            session.add_turn("assistant", result.final_answer)
        return result

    def _node_plan(self, state: _LoopRunState, _context: ExecutionContext) -> _LoopRunState:
        if state.plan is None:
            state.plan = self._build_plan(state.loop_input, state.session)
            state.session.plan_history.append(state.plan)
            state.plan_start_index = 0
            state.skip_approval_for = set()
        state.review_decision = "stop"
        self._checkpoint(
            state,
            node_name="plan",
            status="planned",
            detail=f"steps={len(state.plan.steps)}",
            payload={"plan_id": state.plan.plan_id, "goal": state.loop_input},
        )
        return state

    def _node_execute(self, state: _LoopRunState, _context: ExecutionContext) -> _LoopRunState:
        if state.plan is None:
            state.status = "failed"
            state.last_result = AgentLoopResult(
                status="failed",
                final_answer="missing plan",
                capability_name="",
                run_id=state.run_id,
            )
            self._checkpoint(state, node_name="execute", status="failed", detail="missing plan")
            return state
        result = self._execute_plan(
            state.plan,
            state.session,
            start_index=state.plan_start_index,
            skip_approval_for=state.skip_approval_for,
            run_id=state.run_id,
        )
        result.run_id = state.run_id
        result.plan_id = state.plan.plan_id
        state.last_result = result
        state.status = "running" if result.status == "completed" else result.status
        state.last_observation = result.final_answer
        self._checkpoint(
            state,
            node_name="execute",
            status=result.status,
            detail=result.capability_name,
            payload={"plan_id": state.plan.plan_id, "failed_step_index": result.failed_step_index},
        )
        return state

    def _node_review(self, state: _LoopRunState, _context: ExecutionContext) -> _LoopRunState:
        if state.plan is None or state.last_result is None or state.last_result.status != "completed":
            state.review_decision = "stop"
            self._checkpoint(state, node_name="review", status="skipped", detail="no completed result")
            return state
        review = self._review_plan(state.plan, state.session)
        decision = str(review.get("decision") or "stop")
        state.review_decision = "continue" if decision == "continue" else "stop"
        if state.review_decision == "continue":
            state.loop_input = str(review.get("next_input") or state.last_result.final_answer)
            state.plan = None
            state.plan_start_index = 0
            state.skip_approval_for = set()
            detail = "continue"
        else:
            detail = "stop"
        self._checkpoint(state, node_name="review", status=state.review_decision, detail=detail)
        return state

    def _node_finish(self, state: _LoopRunState, _context: ExecutionContext) -> _LoopRunState:
        if state.last_result is None:
            state.last_result = AgentLoopResult(
                status="cancelled",
                final_answer=state.last_observation or "",
                capability_name="",
                run_id=state.run_id,
                plan_id=state.plan.plan_id if state.plan is not None else None,
            )
        self._checkpoint(
            state,
            node_name="finish",
            status=state.last_result.status,
            detail=state.last_result.capability_name,
            payload={"plan_id": state.last_result.plan_id, "status": state.last_result.status},
        )
        return state

    def _route_after_execute(
        self,
        state: _LoopRunState,
        _context: ExecutionContext,
        _available_actions: list[str],
    ) -> str:
        if state.last_result is None:
            return "finish"
        if state.last_result.status == "completed":
            return "review"
        return "finish"

    def _route_after_review(
        self,
        state: _LoopRunState,
        _context: ExecutionContext,
        _available_actions: list[str],
    ) -> str:
        if state.review_decision == "continue":
            return "plan"
        return "finish"

    def _build_plan(self, user_input: str, session: AssistantSession) -> ExecutionPlan:
        planner = self.context.services.get("planner")
        if callable(planner):
            planned = planner(user_input, session, self.context.capabilities, self.context)
            plan = _normalize_plan(user_input, planned)
            if plan is not None:
                return plan
        capability_name = self._select_capability(user_input, session)
        return ExecutionPlan(
            goal=user_input,
            steps=[PlannedAction(capability_name=capability_name, instruction=user_input)],
        )

    def _execute_plan(
        self,
        plan: ExecutionPlan,
        session: AssistantSession,
        *,
        start_index: int = 0,
        skip_approval_for: set[int] | None = None,
        run_id: str = "",
    ) -> AgentLoopResult:
        last_answer = ""
        last_capability = ""
        normalized: dict[str, Any] = {
            "final_answer": "",
            "execution_trace": [],
            "observations": [],
        }
        for step_index in range(start_index, len(plan.steps)):
            step = plan.steps[step_index]
            capability = self.context.capabilities.require(step.capability_name)
            approval = None
            if step_index not in (skip_approval_for or set()):
                approval = self._maybe_request_approval(session, plan, step_index, step, capability)
            if approval is not None:
                step.status = "awaiting_approval"
                request, token = approval
                return AgentLoopResult(
                    status="needs_approval",
                    final_answer=request.reason,
                    capability_name=capability.name,
                    execution_trace=_plan_trace(plan),
                    approval_request=request,
                    resume_token=token,
                    plan_id=plan.plan_id,
                    failed_step_index=step_index,
                )
            confirmed = session.consume_step_confirmation(plan.plan_id, step_index)
            try:
                self.context.services["step_confirmed"] = confirmed
                task_id = f"{plan.plan_id}:{step_index}"
                self.context.services["run_context"] = {
                    "run_id": run_id,
                    "plan_id": plan.plan_id,
                    "task_id": task_id,
                    "step_index": step_index,
                }
                runner_output = capability.runner(step.instruction, self.context, session)
                normalized = _normalize_runner_output(runner_output)
                self._link_artifacts(run_id, task_id, list(normalized.get("artifact_ids", [])))
                if normalized.get("needs_approval"):
                    manager = self._approval_manager()
                    if manager is None:
                        step.status = "failed"
                        step.observation = "approval manager is not configured"
                        return AgentLoopResult(
                            status="failed",
                            final_answer="approval manager is not configured",
                            capability_name=capability.name,
                            execution_trace=_plan_trace(plan),
                            plan_id=plan.plan_id,
                            failed_step_index=step_index,
                        )
                    request, token = manager.create_request(
                        session=session,
                        plan=plan,
                        step_index=step_index,
                        capability_name=capability.name,
                        instruction=step.instruction,
                        reason=str(normalized.get("approval_reason") or f"{capability.name} requires confirmation"),
                        risk_class=str(normalized.get("risk_class") or capability.risk_class or "high"),
                    )
                    step.status = "awaiting_approval"
                    step.observation = str(normalized.get("final_answer") or request.reason)
                    return AgentLoopResult(
                        status="needs_approval",
                        final_answer=str(normalized.get("final_answer") or request.reason),
                        capability_name=capability.name,
                        execution_trace=_plan_trace(plan) + list(normalized.get("execution_trace", [])),
                        approval_request=request,
                        resume_token=token,
                        plan_id=plan.plan_id,
                        failed_step_index=step_index,
                    )
                last_answer = normalized["final_answer"]
                last_capability = capability.name
                step.status = "completed"
                step.observation = last_answer
                session.focused_capability = capability.name
            except AppError:
                raise
            except Exception as exc:
                step.status = "failed"
                step.observation = str(exc)
                return AgentLoopResult(
                    status="failed",
                    final_answer=f"{capability.name} failed: {exc}",
                    capability_name=capability.name,
                    execution_trace=_plan_trace(plan),
                    plan_id=plan.plan_id,
                    failed_step_index=step_index,
                )
            finally:
                self.context.services.pop("step_confirmed", None)
                self.context.services.pop("run_context", None)
        return AgentLoopResult(
            status="completed",
            final_answer=last_answer,
            capability_name=last_capability,
            execution_trace=_plan_trace(plan) + list(normalized.get("execution_trace", [])),
            plan_id=plan.plan_id,
        )

    def _checkpoint(
        self,
        state: _LoopRunState,
        *,
        node_name: str,
        status: str,
        detail: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        store = self.context.services.get("checkpoint_store")
        if store is None or not all(hasattr(store, method) for method in ("save", "latest", "list_for_run")):
            return
        record = CheckpointRecord(
            run_id=state.run_id,
            session_id=state.session.session_id,
            node_name=node_name,
            status=status,
            step_count=state.step_count,
            detail=detail,
            payload=payload or {},
        )
        cast_store: CheckpointStore = store
        cast_store.save(record)

    def _link_artifacts(self, run_id: str, task_id: str, artifact_ids: list[str]) -> None:
        if not run_id or not task_id or not artifact_ids:
            return
        store = self.context.services.get("checkpoint_store")
        if store is None or not hasattr(store, "link_artifacts"):
            return
        store.link_artifacts(run_id, task_id, artifact_ids)

    def _review_plan(self, plan: ExecutionPlan, session: AssistantSession) -> dict[str, Any]:
        reviewer = self.context.services.get("reviewer")
        if callable(reviewer):
            review = reviewer(plan, session, self.context.capabilities, self.context)
            if isinstance(review, dict):
                return review
        return {"decision": "stop"}

    def _approval_manager(self) -> ApprovalManager | None:
        manager = self.context.services.get("approval_manager")
        if isinstance(manager, ApprovalManager):
            return manager
        return None

    def _maybe_request_approval(
        self,
        session: AssistantSession,
        plan: ExecutionPlan,
        step_index: int,
        step: PlannedAction,
        capability: Any,
    ) -> tuple[ApprovalRequest, ResumeToken] | None:
        approval_manager = self._approval_manager()
        if approval_manager is None:
            return None
        return approval_manager.request_for(session, plan, step_index, step, capability)

    def _select_capability(self, user_input: str, session: AssistantSession) -> str:
        selector = self.context.services.get("capability_selector")
        if callable(selector):
            selected = selector(user_input, session, self.context.capabilities, self.context)
            if selected:
                return str(selected)
        llm_selected = self._select_capability_with_llm(user_input, session)
        if llm_selected is not None:
            return llm_selected
        triggered_skill = self.context.skills.match_triggered(user_input)
        if triggered_skill is not None:
            return f"skill:{triggered_skill.name}"
        default_selected = route_default_capability(user_input, session, self.context.capabilities, self.context)
        if default_selected is not None:
            return default_selected
        if "desktop_content" in self.context.capabilities.names():
            return "desktop_content"
        names = self.context.capabilities.executable_names()
        if not names:
            raise RuntimeError("no capabilities registered")
        return names[0]

    def _select_capability_with_llm(self, user_input: str, session: AssistantSession) -> str | None:
        capabilities = [
            self.context.capabilities.require(name)
            for name in self.context.capabilities.executable_names()
        ]
        if not capabilities:
            return None
        capability_names = [capability.name for capability in capabilities]
        capability_summary = "\n".join(
            (
                f"- name: {capability.name}; "
                f"source: {capability.source}; "
                f"description: {capability.description}; "
                f"safety: {capability.safety_level}; "
                f"input_contract: {capability.input_contract}; "
                f"cost: {capability.cost_hint}; "
                f"latency: {capability.latency_hint}; "
                f"risk: {capability.risk_class}; "
                f"dependency_readiness: {capability.dependency_readiness}; "
                f"output_type: {capability.output_type}"
            )
            for capability in capabilities
        )
        selected = parse_structured_output(
            runtime.client if (runtime := resolve_model_runtime(self.context.application_context, "capability_selector")) is not None else self.context.application_context.llm_client,
            model=runtime.profile.model_name if runtime is not None else self.context.application_context.llm_model,
            system_prompt=(
                "你是桌面 AI 助手的 capability selector。"
                "请只输出合法 JSON，字段为 capability_name。"
                "只能从候选 capability 名称中选择一个。"
            ),
            user_prompt=(
                f"用户输入：{user_input}\n"
                f"候选 capabilities：\n{capability_summary}\n"
                f"最近 capability：{session.focused_capability or ''}"
            ),
            normalizer=lambda parsed: _normalize_capability_name(parsed, capability_names),
            max_tokens=120,
        )
        return selected


def _normalize_capability_name(parsed: dict[str, Any], capability_names: list[str]) -> str | None:
    if not isinstance(parsed, dict):
        return None
    capability_name = str(parsed.get("capability_name") or "").strip()
    if capability_name in capability_names:
        return capability_name
    return None


def _normalize_plan(goal: str, planned: Any) -> ExecutionPlan | None:
    if isinstance(planned, ExecutionPlan):
        return planned
    if not isinstance(planned, list):
        return None
    steps: list[PlannedAction] = []
    for item in planned:
        if isinstance(item, PlannedAction):
            steps.append(item)
            continue
        if not isinstance(item, dict):
            return None
        capability_name = str(item.get("capability_name") or "").strip()
        instruction = str(item.get("instruction") or "").strip()
        if not capability_name or not instruction:
            return None
        steps.append(PlannedAction(capability_name=capability_name, instruction=instruction))
    if not steps:
        return None
    return ExecutionPlan(goal=goal, steps=steps)


def _normalize_runner_output(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return {
            "final_answer": str(output.get("final_answer") or output.get("text") or ""),
            "execution_trace": list(output.get("execution_trace") or []),
            "observations": list(output.get("observations") or []),
            "artifact_ids": list(output.get("artifact_ids") or []),
            "needs_approval": bool(output.get("needs_approval")),
            "approval_reason": str(output.get("approval_reason") or ""),
            "risk_class": str(output.get("risk_class") or ""),
        }
    return {
        "final_answer": str(output or ""),
        "execution_trace": [],
        "observations": [],
        "artifact_ids": [],
        "needs_approval": False,
        "approval_reason": "",
        "risk_class": "",
    }


def _plan_trace(plan: ExecutionPlan) -> list[dict[str, Any]]:
    return [
        {
            "name": f"plan:{index}",
            "status": step.status,
            "detail": f"{step.capability_name} -> {step.instruction}",
        }
        for index, step in enumerate(plan.steps)
    ]
