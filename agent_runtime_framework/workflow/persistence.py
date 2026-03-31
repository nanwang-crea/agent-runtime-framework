from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent_runtime_framework.workflow.models import GoalSpec, NodeResult, NodeState, SubTaskSpec, WorkflowEdge, WorkflowGraph, WorkflowNode, WorkflowRun


class WorkflowPersistenceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, run: WorkflowRun) -> None:
        payload = self._read_all()
        payload[run.run_id] = asdict(run)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, run_id: str) -> WorkflowRun:
        payload = self._read_all().get(run_id)
        if payload is None:
            raise KeyError(run_id)
        return self._restore_run(payload)

    def _read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _restore_run(self, payload: dict[str, Any]) -> WorkflowRun:
        graph_payload = payload.get("graph", {})
        graph = WorkflowGraph(
            nodes=[WorkflowNode(**item) for item in graph_payload.get("nodes", [])],
            edges=[WorkflowEdge(**item) for item in graph_payload.get("edges", [])],
            metadata=dict(graph_payload.get("metadata", {})),
        )
        run = WorkflowRun(
            run_id=str(payload.get("run_id") or ""),
            goal=str(payload.get("goal") or ""),
            graph=graph,
            shared_state=dict(payload.get("shared_state", {})),
            status=str(payload.get("status") or "pending"),
            final_output=payload.get("final_output"),
            error=payload.get("error"),
            metadata=dict(payload.get("metadata", {})),
        )
        for node_id, state_payload in dict(payload.get("node_states", {})).items():
            result_payload = state_payload.get("result")
            run.node_states[node_id] = NodeState(
                node_id=str(state_payload.get("node_id") or node_id),
                status=str(state_payload.get("status") or "pending"),
                result=NodeResult(**result_payload) if isinstance(result_payload, dict) else None,
                error=state_payload.get("error"),
                approval_requested=bool(state_payload.get("approval_requested", False)),
                approval_granted=state_payload.get("approval_granted"),
                attempts=int(state_payload.get("attempts", 0)),
                metadata=dict(state_payload.get("metadata", {})),
            )
        return run
