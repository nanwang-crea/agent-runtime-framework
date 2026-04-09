from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent_runtime_framework.workflow.state.models import (
    GoalSpec,
    NodeState,
    SubTaskSpec,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    restore_interaction_request,
    restore_node_result,
)


class WorkflowPersistenceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, run: WorkflowRun) -> None:
        payload = self._read_all()
        payload[run.run_id] = self._json_safe_run_payload(run)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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

    def _json_safe_run_payload(self, run: WorkflowRun) -> dict[str, Any]:
        payload = asdict(run)
        shared_state = dict(payload.get("shared_state", {}))
        for volatile_key in ("runtime_context", "agent_graph_state_ref"):
            shared_state.pop(volatile_key, None)
        payload["shared_state"] = self._json_safe_value(shared_state)
        payload["metadata"] = self._json_safe_value(dict(payload.get("metadata", {})))
        payload["graph"] = self._json_safe_value(dict(payload.get("graph", {})))
        payload["node_states"] = self._json_safe_value(dict(payload.get("node_states", {})))
        payload["pending_interaction"] = self._json_safe_value(payload.get("pending_interaction"))
        payload["final_output"] = self._json_safe_value(payload.get("final_output"))
        payload["error"] = self._json_safe_value(payload.get("error"))
        return payload

    def _json_safe_value(self, value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): self._json_safe_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._json_safe_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if hasattr(value, "__dict__"):
            return self._json_safe_value(vars(value))
        return str(value)

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
            shared_state=self._restore_shared_state(dict(payload.get("shared_state", {}))),
            status=str(payload.get("status") or "pending"),
            pending_interaction=restore_interaction_request(payload.get("pending_interaction")),
            final_output=payload.get("final_output"),
            error=payload.get("error"),
            metadata=dict(payload.get("metadata", {})),
        )
        for node_id, state_payload in dict(payload.get("node_states", {})).items():
            result_payload = state_payload.get("result")
            run.node_states[node_id] = NodeState(
                node_id=str(state_payload.get("node_id") or node_id),
                status=str(state_payload.get("status") or "pending"),
                result=restore_node_result(result_payload),
                error=state_payload.get("error"),
                approval_requested=bool(state_payload.get("approval_requested", False)),
                approval_granted=state_payload.get("approval_granted"),
                attempts=int(state_payload.get("attempts", 0)),
                metadata=dict(state_payload.get("metadata", {})),
            )
        return run


    def _restore_shared_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        restored: dict[str, Any] = {}
        for key, value in payload.items():
            restored[key] = self._restore_value(value)
        return restored

    def _restore_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            if "status" in value and ("output" in value or "references" in value or "approval_data" in value):
                return restore_node_result(value)
            if "kind" in value and "prompt" in value:
                return restore_interaction_request(value)
            return {key: self._restore_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._restore_value(item) for item in value]
        return value
