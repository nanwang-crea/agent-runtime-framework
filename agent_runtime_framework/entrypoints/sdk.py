from __future__ import annotations

from agent_runtime_framework.entrypoints.models import AgentRequest, AgentResponse


def run_agent_request(app, request: AgentRequest) -> AgentResponse:
    if getattr(app, "_active_agent", None) != request.agent_id:
        app.switch_context(agent_profile=request.agent_id)
    payload = app.chat(request.message)
    return AgentResponse(
        status=str(payload.get("status") or "completed"),
        agent_id=request.agent_id,
        output=str(payload.get("final_answer") or ""),
        metadata=dict(payload),
    )
