from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.api.services.chat_service import ChatService
from agent_runtime_framework.api.services.context_service import ContextService
from agent_runtime_framework.api.services.run_service import RunService
from agent_runtime_framework.api.services.session_service import SessionService


@dataclass(slots=True)
class ApiServices:
    session: SessionService
    chat: ChatService
    context: ContextService
    runs: RunService
    model_center: Any
