from __future__ import annotations

from dataclasses import dataclass

from agent_runtime_framework.api.responses.session_responses import SessionResponseFactory
from agent_runtime_framework.api.services.chat_service import ChatService
from agent_runtime_framework.api.services.context_service import ContextService
from agent_runtime_framework.api.services.model_center_service import ModelCenterService
from agent_runtime_framework.api.services.run_service import RunService


@dataclass(slots=True)
class ApiServices:
    session: SessionResponseFactory
    chat: ChatService
    context: ContextService
    runs: RunService
    model_center: ModelCenterService
