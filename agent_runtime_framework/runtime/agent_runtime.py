from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime_framework.entrypoints.models import AgentRequest, AgentResponse
from agent_runtime_framework.runtime.agent_sessions import AgentSessionRecord
from agent_runtime_framework.runtime.subagents import SubagentLink


@dataclass(slots=True)
class AgentRuntime:
    app: Any
    sessions: dict[str, AgentSessionRecord] = field(default_factory=dict)
    links: list[SubagentLink] = field(default_factory=list)

    def run_agent(self, request: AgentRequest) -> AgentResponse:
        session = AgentSessionRecord(agent_id=request.agent_id, goal=request.message)
        response = self._invoke(request)
        session.run_id = str(response.metadata.get("run_id") or "")
        self.sessions[session.session_id] = session
        response.metadata.setdefault("session_id", session.session_id)
        return response

    def resume_agent(self, token_id: str, *, approved: bool) -> AgentResponse:
        payload = self.app.approve(token_id, approved=approved)
        return AgentResponse(status=str(payload.get("status") or "completed"), agent_id=str(self.app._active_agent), output=str(payload.get("final_answer") or ""), metadata=dict(payload))

    def fork_subagent(self, parent_session_id: str, *, agent_id: str, goal: str) -> AgentResponse:
        response = self.run_agent(AgentRequest(message=goal, agent_id=agent_id, metadata={"parent_session_id": parent_session_id}))
        child_session_id = str(response.metadata.get("session_id") or "")
        if child_session_id:
            self.links.append(SubagentLink(parent_session_id=parent_session_id, child_session_id=child_session_id, agent_id=agent_id))
            self.sessions[child_session_id].parent_session_id = parent_session_id
        return response

    def _invoke(self, request: AgentRequest) -> AgentResponse:
        from agent_runtime_framework.entrypoints.sdk import run_agent_request
        return run_agent_request(self.app, request)
