from pathlib import Path
from types import SimpleNamespace

from agent_runtime_framework.workflow import WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.aggregator import aggregate_node_results
from agent_runtime_framework.workflow.evidence_synthesis_executor import EvidenceSynthesisExecutor
from agent_runtime_framework.workflow.models import NodeResult


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


def test_evidence_synthesis_executor_prefers_model_summary_when_available():
    run = WorkflowRun(goal="解释 README.md 在讲什么")
    run.shared_state["aggregated_result"] = aggregate_node_results(
        [
            NodeResult(status="completed", output={"facts": [{"kind": "file", "path": "README.md"}], "evidence_items": [{"kind": "path", "path": "README.md", "summary": "README 提到 workflow runtime。"}]}, references=["README.md"])
        ]
    )
    node = WorkflowNode(node_id="evidence_synthesis", node_type="evidence_synthesis")
    client = _FakeChatClient("README.md 介绍了这个项目的 workflow runtime 能力与用途。")

    result = EvidenceSynthesisExecutor().execute(node, run, _model_context(client))

    assert result.output["summary"] == "README.md 介绍了这个项目的 workflow runtime 能力与用途。"
    assert run.shared_state["evidence_synthesis"]["summary"] == result.output["summary"]
    assert "response_synthesis" not in run.shared_state
    assert client.requests
