from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_PROMPTS_DIR = Path(__file__).with_name("prompts")


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    task_profile: str
    instructions: str


class WorkflowRegistry:
    def __init__(self, workflows: list[WorkflowDefinition]) -> None:
        self._by_task_profile = {workflow.task_profile: workflow for workflow in workflows}
        self._by_name = {workflow.name: workflow for workflow in workflows}

    def get_for_task_profile(self, task_profile: str) -> WorkflowDefinition | None:
        return self._by_task_profile.get(str(task_profile or "").strip())

    def require_for_task_profile(self, task_profile: str) -> WorkflowDefinition:
        workflow = self.get_for_task_profile(task_profile)
        if workflow is None:
            raise KeyError(f"unknown workflow for task_profile: {task_profile}")
        return workflow

    @classmethod
    def default(cls) -> "WorkflowRegistry":
        return cls(
            [
                WorkflowDefinition(name="repository_overview", task_profile="repository_explainer", instructions=_load_workflow_doc("repository_overview")),
                WorkflowDefinition(name="file_reader", task_profile="file_reader", instructions=_load_workflow_doc("file_reader")),
                WorkflowDefinition(name="change_and_verify", task_profile="change_and_verify", instructions=_load_workflow_doc("change_and_verify")),
                WorkflowDefinition(name="debug_and_fix", task_profile="debug_and_fix", instructions=_load_workflow_doc("debug_and_fix")),
                WorkflowDefinition(name="multi_file_change", task_profile="multi_file_change", instructions=_load_workflow_doc("multi_file_change")),
                WorkflowDefinition(name="test_and_verify", task_profile="test_and_verify", instructions=_load_workflow_doc("test_and_verify")),
            ]
        )


def workflow_name_for_task_profile(task_profile: str) -> str:
    workflow = WorkflowRegistry.default().get_for_task_profile(task_profile)
    return workflow.name if workflow is not None else ""


@lru_cache(maxsize=16)
def _load_workflow_doc(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
