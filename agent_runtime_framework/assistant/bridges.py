from __future__ import annotations

from typing import Any

from agent_runtime_framework.assistant.capabilities import CapabilitySpec


def create_codex_delegate_capability(name: str = "codex_task") -> CapabilitySpec:
    def _runner(user_input: str, context: Any, session: Any) -> dict[str, Any]:
        runner = context.services.get("codex_task_runner")
        if not callable(runner):
            raise RuntimeError("codex_task_runner is not configured")
        result = runner(user_input, context, session)
        if isinstance(result, dict):
            return result
        return {
            "final_answer": str(result or ""),
            "execution_trace": [
                {
                    "name": name,
                    "status": "completed",
                    "detail": "delegated to codex runtime",
                }
            ],
            "observations": [],
        }

    return CapabilitySpec(
        name=name,
        runner=_runner,
        source="bridge",
        description="Delegate task-oriented execution to the Codex action runtime.",
        safety_level="delegated",
        input_contract={"mode": "task_execution"},
        cost_hint="medium",
        latency_hint="medium",
        risk_class="moderate",
        dependency_readiness="ready",
        output_type="delegated_task",
    )
