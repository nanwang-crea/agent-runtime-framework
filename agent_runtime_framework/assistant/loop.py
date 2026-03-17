from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.approval import ApprovalManager, ApprovalRequest, ResumeToken
from agent_runtime_framework.assistant.capabilities import CapabilityRegistry
from agent_runtime_framework.assistant.session import AssistantSession, ExecutionPlan, PlannedAction
from agent_runtime_framework.assistant.skills import SkillRegistry
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
    approval_request: ApprovalRequest | None = None
    resume_token: ResumeToken | None = None


class AgentLoop:
    def __init__(self, context: AssistantContext) -> None:
        self.context = context

    def run(self, user_input: str) -> AgentLoopResult:
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        session.add_turn("user", user_input)
        return self._run_for_session(user_input, session)

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
            )
        self.context.session = pending.session
        return self._execute_plan(
            pending.plan,
            pending.session,
            start_index=pending.step_index,
            skip_approval_for={pending.step_index},
        )

    def _run_for_session(self, user_input: str, session: AssistantSession) -> AgentLoopResult:
        loop_input = user_input
        while True:
            plan = self._build_plan(loop_input, session)
            session.plan_history.append(plan)
            result = self._execute_plan(plan, session)
            if result.status != "completed":
                return result
            review = self._review_plan(plan, session)
            if review.get("decision") != "continue":
                session.add_turn("assistant", result.final_answer)
                return result
            loop_input = str(review.get("next_input") or result.final_answer)

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
    ) -> AgentLoopResult:
        last_answer = ""
        last_capability = ""
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
                    approval_request=request,
                    resume_token=token,
                )
            last_answer = capability.runner(step.instruction, self.context, session)
            last_capability = capability.name
            step.status = "completed"
            step.observation = last_answer
            session.focused_capability = capability.name
        return AgentLoopResult(
            status="completed",
            final_answer=last_answer,
            capability_name=last_capability,
        )

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
        if "desktop_content" in self.context.capabilities.names():
            return "desktop_content"
        names = self.context.capabilities.names()
        if not names:
            raise RuntimeError("no capabilities registered")
        return names[0]

    def _select_capability_with_llm(self, user_input: str, session: AssistantSession) -> str | None:
        capabilities = [
            self.context.capabilities.require(name)
            for name in self.context.capabilities.names()
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
