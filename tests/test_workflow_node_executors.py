from pathlib import Path

from agent_runtime_framework.workflow import WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.node_executors import FileReadExecutor, WorkspaceOverviewExecutor


def test_workspace_overview_executor_produces_directory_evidence(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    node = WorkflowNode(node_id="repository_overview", node_type="workspace_overview")
    run = WorkflowRun(goal="overview")

    result = WorkspaceOverviewExecutor().execute(node, run, {"workspace_root": str(tmp_path)})

    assert result.output["entries"]
    assert any("README.md" in reference for reference in result.references)
    assert any(entry.endswith("src/") for entry in result.output["entries"])


def test_file_read_executor_reads_readme_and_returns_references(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# Demo\nhello\n", encoding="utf-8")
    node = WorkflowNode(
        node_id="file_read",
        node_type="file_read",
        metadata={"target_path": "README.md"},
    )
    run = WorkflowRun(goal="read readme")

    result = FileReadExecutor().execute(node, run, {"workspace_root": str(tmp_path)})

    assert result.output["path"] == "README.md"
    assert "hello" in result.output["content"]
    assert "README.md" in result.references[0]
