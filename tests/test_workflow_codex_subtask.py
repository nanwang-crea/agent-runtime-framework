from pathlib import Path

from agent_runtime_framework.agents.codex.loop import CodexAgentLoopResult
from agent_runtime_framework.agents.codex.models import CodexTask, EvidenceItem, TaskState
from agent_runtime_framework.workflow import WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.codex_subtask import CodexSubtaskExecutor


class StubCodexLoop:
    def run(self, goal: str) -> CodexAgentLoopResult:
        task = CodexTask(goal=goal, actions=[], task_profile="file_reader", state=TaskState())
        task.summary = "README summary"
        task.state.evidence_items.append(
            EvidenceItem(source="workspace", kind="file", summary="README", path="README.md", content="# Demo")
        )
        return CodexAgentLoopResult(
            status="completed",
            final_output="README summary",
            task=task,
            action_kind="respond",
            run_id="run-1",
        )


def test_codex_subtask_executor_wraps_codex_loop_result(tmp_path: Path):
    node = WorkflowNode(
        node_id="codex_file_read",
        node_type="codex_subtask",
        task_profile="file_reader",
        metadata={"goal": "读取 README.md 并总结"},
    )
    run = WorkflowRun(goal="outer workflow")

    result = CodexSubtaskExecutor(codex_loop=StubCodexLoop()).execute(
        node,
        run,
        {"workspace_root": str(tmp_path)},
    )

    assert result.output["summary"] == "README summary"
    assert result.output["evidence_items"][0]["path"] == "README.md"
    assert result.references == ["README.md"]
