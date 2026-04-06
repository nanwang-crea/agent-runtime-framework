from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_runtime_framework.demo.pending_run_registry import PendingRunRegistry


PayloadFn = Callable[[Any], dict[str, Any]]
RecordRunFn = Callable[[dict[str, Any], str], None]
RememberRunFn = Callable[[str, Any], None]
LoadRunFn = Callable[[str], Any]
ChatFn = Callable[[str], dict[str, Any]]
MemoryPayloadFn = Callable[[], dict[str, Any]]
PlanHistoryFn = Callable[[], list[dict[str, Any]]]
RunHistoryFn = Callable[[], list[dict[str, Any]]]
SessionPayloadFn = Callable[[], dict[str, Any]]


@dataclass(slots=True)
class RunLifecycleService:
    pending_run_registry: PendingRunRegistry
    run_inputs: dict[str, str]
    workflow_payload: PayloadFn
    record_run: RecordRunFn
    remember_workflow_run: RememberRunFn
    load_workflow_run: LoadRunFn
    chat: ChatFn
    session_payload: SessionPayloadFn
    plan_history_payload: PlanHistoryFn
    run_history_payload: RunHistoryFn
    memory_payload: MemoryPayloadFn
    workspace: str

    def approve(self, token_id: str, approved: bool) -> dict[str, Any]:
        token = self.pending_run_registry.consume(token_id)
        if token is None:
            return self._missing_token_payload()
        runtime = token["runtime"]
        run = token["run"]
        resume_token = token["token"]
        resumed = runtime.resume(run, resume_token=resume_token, approved=approved)
        action = f"approval:{'approve' if approved else 'reject'}"
        self.remember_workflow_run(action, resumed)
        payload = self.workflow_payload(resumed)
        self.record_run(payload, action)
        return payload

    def replay(self, run_id: str) -> dict[str, Any]:
        try:
            restored = self.load_workflow_run(run_id)
        except Exception:
            prompt = self.run_inputs.get(run_id)
            if not prompt:
                return self._missing_run_payload()
            payload = self.chat(prompt)
            self.record_run(payload, f"replay:{run_id}")
            return payload
        payload = self.workflow_payload(restored)
        self.record_run(payload, f"replay:{run_id}")
        return payload

    def _missing_token_payload(self) -> dict[str, Any]:
        return {
            "status": "missing_token",
            "final_answer": "未找到可恢复的审批请求。",
            "capability_name": "",
            "execution_trace": [],
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "approval_request": None,
            "resume_token_id": None,
            "workspace": self.workspace,
        }

    def _missing_run_payload(self) -> dict[str, Any]:
        return {
            "status": "missing_run",
            "final_answer": "未找到可重放的运行记录。",
            "capability_name": "",
            "execution_trace": [],
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "approval_request": None,
            "resume_token_id": None,
            "workspace": self.workspace,
        }
