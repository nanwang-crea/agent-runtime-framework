from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.capabilities import CapabilityRegistry
from agent_runtime_framework.assistant.session import AssistantSession
from agent_runtime_framework.assistant.skills import SkillRegistry
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


class AgentLoop:
    def __init__(self, context: AssistantContext) -> None:
        self.context = context

    def run(self, user_input: str) -> AgentLoopResult:
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        session.add_turn("user", user_input)

        capability_name = self._select_capability(user_input, session)
        capability = self.context.capabilities.require(capability_name)
        final_answer = capability.runner(user_input, self.context, session)

        session.focused_capability = capability_name
        session.add_turn("assistant", final_answer)
        return AgentLoopResult(
            status="completed",
            final_answer=final_answer,
            capability_name=capability_name,
        )

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
                f"input_contract: {capability.input_contract}"
            )
            for capability in capabilities
        )
        selected = parse_structured_output(
            self.context.application_context.llm_client,
            model=self.context.application_context.llm_model,
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
