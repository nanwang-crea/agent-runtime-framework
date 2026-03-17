from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.capabilities import CapabilityRegistry
from agent_runtime_framework.assistant.session import AssistantSession
from agent_runtime_framework.assistant.skills import SkillRegistry


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
        if "desktop_content" in self.context.capabilities.names():
            return "desktop_content"
        names = self.context.capabilities.names()
        if not names:
            raise RuntimeError("no capabilities registered")
        return names[0]
