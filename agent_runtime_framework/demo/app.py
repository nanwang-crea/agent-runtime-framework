from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext, create_desktop_content_application
from agent_runtime_framework.assistant import (
    AgentLoop,
    AssistantContext,
    AssistantSession,
    CapabilityRegistry,
    SkillRegistry,
    create_conversation_capability,
    route_default_capability,
)
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry


@dataclass(slots=True)
class DemoAssistantApp:
    workspace: Path
    context: AssistantContext
    _pending_tokens: dict[str, Any]

    def chat(self, message: str) -> dict[str, Any]:
        result = AgentLoop(self.context).run(message)
        payload = self._result_payload(result)
        if result.resume_token is not None:
            self._pending_tokens[result.resume_token.token_id] = result.resume_token
        return payload

    def approve(self, token_id: str, approved: bool) -> dict[str, Any]:
        token = self._pending_tokens.pop(token_id, None)
        if token is None:
            return {
                "status": "missing_token",
                "final_answer": "未找到可恢复的审批请求。",
                "capability_name": "",
                "session": self.session_payload(),
                "plan_history": self.plan_history_payload(),
            }
        result = AgentLoop(self.context).resume(token, approved=approved)
        return self._result_payload(result)

    def session_payload(self) -> dict[str, Any]:
        session = self.context.session
        if session is None:
            return {"session_id": None, "turns": []}
        return {
            "session_id": session.session_id,
            "turns": [
                {"role": turn.role, "content": turn.content}
                for turn in session.turns
            ],
        }

    def plan_history_payload(self) -> list[dict[str, Any]]:
        session = self.context.session
        if session is None:
            return []
        return [
            {
                "plan_id": plan.plan_id,
                "goal": plan.goal,
                "steps": [
                    {
                        "capability_name": step.capability_name,
                        "instruction": step.instruction,
                        "status": step.status,
                        "observation": step.observation,
                    }
                    for step in plan.steps
                ],
            }
            for plan in session.plan_history
        ]

    def _result_payload(self, result: Any) -> dict[str, Any]:
        approval_request = None
        resume_token_id = None
        if result.approval_request is not None:
            approval_request = {
                "capability_name": result.approval_request.capability_name,
                "instruction": result.approval_request.instruction,
                "reason": result.approval_request.reason,
                "risk_class": result.approval_request.risk_class,
            }
        if result.resume_token is not None:
            resume_token_id = result.resume_token.token_id
        return {
            "status": result.status,
            "final_answer": result.final_answer,
            "capability_name": result.capability_name,
            "approval_request": approval_request,
            "resume_token_id": resume_token_id,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "workspace": str(self.workspace),
        }


def create_demo_assistant_app(workspace: str | Path) -> DemoAssistantApp:
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace_path}")
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace_path]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace_path)},
    )
    context = AssistantContext(
        application_context=app_context,
        capabilities=CapabilityRegistry(),
        skills=SkillRegistry(),
        services={"capability_selector": route_default_capability},
        session=AssistantSession(session_id=str(uuid4())),
    )
    context.capabilities.register(create_conversation_capability())
    context.capabilities.register_application("desktop_content", create_desktop_content_application())
    return DemoAssistantApp(
        workspace=workspace_path,
        context=context,
        _pending_tokens={},
    )
