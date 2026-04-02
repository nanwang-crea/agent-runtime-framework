from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


BuildRuntimeFn = Callable[[], Any]


@dataclass(slots=True)
class PendingRunRegistry:
    entries: dict[str, dict[str, Any]]
    build_agent_graph_runtime: BuildRuntimeFn
    build_graph_execution_runtime: BuildRuntimeFn

    def register(self, run: Any) -> str | None:
        if getattr(run, "status", None) != "waiting_approval":
            return None
        resume_token = getattr(run, "shared_state", {}).get("resume_token")
        if resume_token is None:
            return None
        token_kind = "agent_graph" if getattr(run, "metadata", {}).get("pending_subrun") is not None else "workflow"
        runtime = self.build_agent_graph_runtime() if token_kind == "agent_graph" else self.build_graph_execution_runtime()
        self.entries[resume_token.token_id] = {"kind": token_kind, "runtime": runtime, "run": run, "token": resume_token}
        return resume_token.token_id

    def consume(self, token_id: str) -> dict[str, Any] | None:
        return self.entries.pop(token_id, None)
