from pathlib import Path
from types import SimpleNamespace

from agent_runtime_framework.workflow import WorkflowNode, WorkflowRun
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
