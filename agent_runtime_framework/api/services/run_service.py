from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.api.presenters.response_builder import ApiResponseBuilder
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState
from agent_runtime_framework.api.services.chat_service import ChatService


@dataclass(slots=True)
class RunService:
    runtime_state: ApiRuntimeState
    response_builder: ApiResponseBuilder
    chat_service: ChatService

    def _workflow_payload(self, run: Any) -> dict[str, Any]:
        return self.chat_service._workflow_payload(run, resume_token_id=self.chat_service._register_pending_run(run))

    def _remember_run(self, message: str, run: Any) -> None:
        self.chat_service._remember_workflow_run(message, run)

    def _missing_token_payload(self) -> dict[str, Any]:
        return {
            "status": "missing_token",
            "final_answer": "未找到可恢复的审批请求。",
            "execution_trace": [],
            "session": self.response_builder.session_payload(),
            "plan_history": self.response_builder.plan_history_payload(),
            "run_history": self.response_builder.run_history_payload(),
            "memory": self.response_builder.memory_payload(),
            "approval_request": None,
            "resume_token_id": None,
            "workspace": str(self.runtime_state.workspace),
        }

    def _missing_run_payload(self) -> dict[str, Any]:
        return {
            "status": "missing_run",
            "final_answer": "未找到可重放的运行记录。",
            "execution_trace": [],
            "session": self.response_builder.session_payload(),
            "plan_history": self.response_builder.plan_history_payload(),
            "run_history": self.response_builder.run_history_payload(),
            "memory": self.response_builder.memory_payload(),
            "approval_request": None,
            "resume_token_id": None,
            "workspace": str(self.runtime_state.workspace),
        }

    def approve(self, token_id: str, approved: bool) -> dict[str, Any]:
        token = self.runtime_state._pending_tokens.pop(token_id, None)
        if token is None:
            return self._missing_token_payload()
        runtime = token["runtime"]
        run = token["run"]
        resume_token = token["token"]
        resumed = runtime.resume(run, resume_token=resume_token, approved=approved)
        action = f"approval:{'approve' if approved else 'reject'}"
        self._remember_run(action, resumed)
        payload = self._workflow_payload(resumed)
        self.runtime_state.record_run(payload, action)
        return payload

    def replay(self, run_id: str) -> dict[str, Any]:
        try:
            restored = self.runtime_state._workflow_store.load(run_id)
        except Exception:
            prompt = self.runtime_state._run_inputs.get(run_id)
            if not prompt:
                return self._missing_run_payload()
            payload = self.chat_service.chat(prompt)
            self.runtime_state.record_run(payload, f"replay:{run_id}")
            return payload
        payload = self._workflow_payload(restored)
        self.runtime_state.record_run(payload, f"replay:{run_id}")
        return payload
