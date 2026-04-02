from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_runtime_framework.memory.index import MemoryRecord
from agent_runtime_framework.resources import ResourceRef


@dataclass(slots=True)
class WorkflowRunObserver:
    context: Any
    workspace: Path
    task_history: list[Any]

    def capture_workflow_codex_history(self, run: Any) -> None:
        results = run.shared_state.get("workspace_subtask_results", {})
        for result in results.values():
            task = getattr(result, "task", None)
            if task is None:
                continue
            self.task_history.insert(0, task)
        self.task_history[:] = self.task_history[:40]

    def remember_workflow_run(self, message: str, run: Any) -> None:
        session = self.context.session
        if session is not None:
            session.add_turn("user", message)
            if run.final_output:
                session.add_turn("assistant", str(run.final_output))
            session.focused_capability = "workflow"
        pseudo_actions = []
        references: list[str] = []
        node_results = run.shared_state.get("node_results", {})
        for node in run.graph.nodes:
            state = run.node_states.get(node.node_id)
            if state is None:
                continue
            result = node_results.get(node.node_id)
            observation = ""
            if result is not None:
                if isinstance(result.output, dict):
                    observation = str(result.output.get("summary") or result.output.get("final_response") or result.output.get("content") or "")
                elif result.output is not None:
                    observation = str(result.output)
                for reference in getattr(result, "references", []):
                    if reference and reference not in references:
                        references.append(reference)
            pseudo_actions.append(SimpleNamespace(kind=node.node_type, instruction=message, status=getattr(state, "status", "pending"), observation=observation, metadata={}))
        workflow_task = SimpleNamespace(task_id=run.run_id, goal=message, actions=pseudo_actions)
        self.task_history.insert(0, workflow_task)
        self.task_history[:] = self.task_history[:40]
        if references:
            ref = ResourceRef.for_path(references[0])
            summary = str(run.final_output or f"Workflow completed for {ref.title}")
            self.context.application_context.session_memory.remember_focus([ref], summary=summary)
            remember = getattr(self.context.application_context.index_memory, "remember", None)
            if callable(remember):
                resolved = Path(ref.location).resolve()
                path = str(resolved.relative_to(self.workspace)) if resolved.is_relative_to(self.workspace) else ref.location
                remember(MemoryRecord(key=f"focus:{path}", text=f"{path} {summary}".strip(), kind="workspace_focus", metadata={"path": path, "summary": summary}))
