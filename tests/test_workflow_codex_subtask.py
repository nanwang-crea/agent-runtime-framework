from pathlib import Path

from agent_runtime_framework.agents.workspace_backend.models import WorkspaceTask, EvidenceItem, TaskState
from agent_runtime_framework.workflow import WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.workspace_subtask import WorkspaceSubtaskExecutor, WorkspaceSubtaskResult


class StubCodexLoop:
    def __call__(self, goal: str, *, task_profile: str, metadata: dict[str, object]) -> WorkspaceSubtaskResult:
        task = WorkspaceTask(goal=goal, actions=[], task_profile="chunked_file_read", state=TaskState())
        task.summary = "README summary"
        task.state.evidence_items.append(
            EvidenceItem(source="workspace", kind="file", summary="README", path="README.md", content="# Demo")
        )
        return WorkspaceSubtaskResult(
            status="completed",
            final_output="README summary",
            task=task,
            action_kind="respond",
            run_id="run-1",
        )


def test_workspace_subtask_executor_wraps_workspace_loop_result(tmp_path: Path):
    node = WorkflowNode(
        node_id="workspace_file_read",
        node_type="workspace_subtask",
        task_profile="chunked_file_read",
        metadata={"goal": "读取 README.md 并总结"},
    )
    run = WorkflowRun(goal="outer workflow")

    result = WorkspaceSubtaskExecutor(run_subtask=StubCodexLoop()).execute(
        node,
        run,
        {"workspace_root": str(tmp_path)},
    )

    assert result.output["summary"] == "README summary"
    assert result.output["evidence_items"][0]["path"] == "README.md"
    assert result.references == ["README.md"]



def test_workspace_subtask_executor_exposes_bridge_metadata(tmp_path: Path):
    node = WorkflowNode(
        node_id="workspace_change",
        node_type="workspace_subtask",
        task_profile="change_and_verify",
        metadata={
            "goal": "编辑 README.md 并验证修改结果",
            "fallback_reason": "unsupported_primary_intent",
        },
    )
    run = WorkflowRun(goal="outer workflow")

    result = WorkspaceSubtaskExecutor(run_subtask=StubCodexLoop()).execute(
        node,
        run,
        {"workspace_root": str(tmp_path)},
    )

    assert result.output["summary"] == "README summary"
    assert result.output["workspace_status"] == "completed"
