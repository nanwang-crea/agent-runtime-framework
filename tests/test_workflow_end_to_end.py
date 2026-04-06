from pathlib import Path

from agent_runtime_framework.demo.app import create_demo_assistant_app
from agent_runtime_framework.workflow.models import JudgeDecision


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


def test_public_surface_marks_graph_execution_runtime_as_primary():
    import agent_runtime_framework as arf

    assert hasattr(arf, "GraphExecutionRuntime")
    assert hasattr(arf, "WorkflowRun")


def test_demo_app_create_file_request_uses_graph_native_create_path(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_demo_assistant_app(workspace)

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.agent_graph_runtime.judge_progress",
        lambda goal_envelope, aggregated_payload, graph_state: JudgeDecision(status="accepted", reason="created"),
    )

    payload = app.chat("创建 docs/notes.md")

    assert payload["status"] == "completed"
    assert any(step["name"] == "create_path" for step in payload["execution_trace"])
    assert (workspace / "docs" / "notes.md").exists()


def test_demo_app_move_request_uses_graph_native_move_path(tmp_path: Path, monkeypatch):
    from agent_runtime_framework.workflow.models import GoalSpec

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "docs").mkdir()
    (workspace / "docs" / "notes.md").write_text("hello\n", encoding="utf-8")
    app = create_demo_assistant_app(workspace)

    monkeypatch.setattr(
        "agent_runtime_framework.demo.app.analyze_goal",
        lambda _message, context=None: GoalSpec(
            original_goal="把 docs/notes.md 移动到 docs/archive/notes.md",
            primary_intent="change_and_verify",
            target_paths=["docs/notes.md"],
        ),
    )

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.agent_graph_runtime.judge_progress",
        lambda goal_envelope, aggregated_payload, graph_state: JudgeDecision(status="accepted", reason="moved"),
    )

    payload = app.chat("把 docs/notes.md 移动到 docs/archive/notes.md")

    assert payload["status"] == "completed"
    assert any(step["name"] == "move_path" for step in payload["execution_trace"])
    assert not (workspace / "docs" / "notes.md").exists()
    assert (workspace / "docs" / "archive" / "notes.md").exists()
