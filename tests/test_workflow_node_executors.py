from pathlib import Path
from types import SimpleNamespace

from agent_runtime_framework.workflow import WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.node_executors import FileReadExecutor, WorkspaceOverviewExecutor
from agent_runtime_framework.workflow.response_synthesis_executor import ResponseSynthesisExecutor


class _FakeChatClient:
    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.requests = []

    def create_chat_completion(self, request):
        self.requests.append(request)
        content = self._responses.pop(0) if self._responses else ""
        return SimpleNamespace(content=content)


def _model_context(client: _FakeChatClient):
    return {"application_context": SimpleNamespace(llm_client=client, llm_model="demo-model", services={})}


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


def test_workspace_overview_executor_prefers_model_summary_when_available(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    node = WorkflowNode(node_id="repository_overview", node_type="workspace_overview")
    run = WorkflowRun(goal="帮我概览一下仓库结构")
    client = _FakeChatClient("这个仓库包含 README 和 src 目录，入口代码位于 src/app.py。")

    result = WorkspaceOverviewExecutor().execute(node, run, {"workspace_root": str(tmp_path), **_model_context(client)})

    assert result.output["summary"] == "这个仓库包含 README 和 src 目录，入口代码位于 src/app.py。"
    assert client.requests


def test_file_read_executor_prefers_model_summary_when_available(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# Demo\nThis project demonstrates workflow runtime.\n", encoding="utf-8")
    node = WorkflowNode(node_id="file_read", node_type="file_read", metadata={"target_path": "README.md"})
    run = WorkflowRun(goal="总结 README 在讲什么")
    client = _FakeChatClient("README 主要介绍这个项目演示了 workflow runtime。")

    result = FileReadExecutor().execute(node, run, {"workspace_root": str(tmp_path), **_model_context(client)})

    assert result.output["summary"] == "README 主要介绍这个项目演示了 workflow runtime。"
    assert client.requests


def test_response_synthesis_executor_prefers_model_summary_when_available():
    run = WorkflowRun(goal="解释 README.md 在讲什么")
    run.shared_state["node_results"] = {
        "target_resolution": SimpleNamespace(output={"resolution_status": "resolved", "resolved_path": "README.md", "summary": "定位到 README.md"}, references=["README.md"]),
        "file_inspection": SimpleNamespace(output={"path": "README.md", "resolved_kind": "file", "summary": "README 提到 workflow runtime。", "text": "README 提到 workflow runtime。"}, references=["README.md"]),
    }
    node = WorkflowNode(node_id="response_synthesis", node_type="response_synthesis")
    client = _FakeChatClient("README.md 介绍了这个项目的 workflow runtime 能力与用途。")

    result = ResponseSynthesisExecutor().execute(node, run, _model_context(client))

    assert result.output["summary"] == "README.md 介绍了这个项目的 workflow runtime 能力与用途。"
    assert run.shared_state["response_synthesis"]["summary"] == result.output["summary"]
    assert client.requests
