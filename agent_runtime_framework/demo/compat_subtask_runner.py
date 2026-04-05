from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent_runtime_framework.agents.workspace_backend import WorkspaceAction
from agent_runtime_framework.agents.workspace_backend.models import EvidenceItem, TaskState, WorkspaceTask
from agent_runtime_framework.workflow.workspace_subtask import WorkspaceSubtaskResult


@dataclass(slots=True)
class CompatSubtaskRunner:
    def run_subtask(self, goal: str, *, task_profile: str, metadata: dict[str, Any]) -> WorkspaceSubtaskResult:
        summary = str(metadata.get("summary") or goal)
        target_path = str(metadata.get("target_path") or metadata.get("path") or "").strip()
        state = TaskState()
        if target_path:
            state.resolved_target = target_path
            state.evidence_items.append(
                EvidenceItem(source="workflow", kind="path", summary=target_path, path=target_path)
            )
        action = WorkspaceAction(
            kind="workspace_subtask",
            instruction=goal,
            status="completed",
            observation=summary,
            metadata={"direct_output": True},
        )
        task = WorkspaceTask(goal=goal, actions=[action], task_profile=task_profile, state=state)
        task.summary = summary
        return WorkspaceSubtaskResult(
            status="completed",
            final_output=summary,
            task=task,
            action_kind="workspace_subtask",
            run_id=str(uuid4()),
        )
