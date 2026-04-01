from pathlib import Path

from agent_runtime_framework.demo.app import create_demo_assistant_app


def test_demo_app_routes_compound_goal_through_workflow_runtime(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\nThis project demonstrates workflow runtime.\n", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")

    app = create_demo_assistant_app(workspace)
    payload = app.chat("帮我列一下当前文件夹都有什么，以及读取一下README文件并总结告诉我在讲什么")

    assert payload["status"] == "completed"
    assert payload["runtime"] == "workflow"
    assert "README" in payload["final_answer"] or "Demo" in payload["final_answer"]
    assert any(step["detail"] == "workspace_discovery" for step in payload["execution_trace"])
    assert any(step["detail"] == "content_search" for step in payload["execution_trace"])
    assert any(step["detail"] == "chunked_file_read" for step in payload["execution_trace"])


def test_public_surface_marks_workflow_runtime_as_primary():
    import agent_runtime_framework as arf

    assert hasattr(arf, "WorkflowRuntime")
    assert hasattr(arf, "WorkflowRun")
