from agent_runtime_framework.workflow.orchestration.aggregation import aggregate_node_results
from agent_runtime_framework.workflow.state.models import NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.nodes.core import AggregationExecutor, FinalResponseExecutor
from types import SimpleNamespace


class _FakeChatClient:
    def __init__(self, response: str):
        self.response = response
        self.requests = []

    def create_chat_completion(self, request):
        self.requests.append(request)
        return SimpleNamespace(content=self.response)


def test_aggregator_merges_subtask_results_with_references():
    overview_result = NodeResult(
        status=NODE_STATUS_COMPLETED,
        output={"summary": "repo overview"},
        references=["README.md", "src/app.py"],
    )
    readme_result = NodeResult(
        status=NODE_STATUS_COMPLETED,
        output={"summary": "readme summary"},
        references=["README.md"],
    )

    result = aggregate_node_results([overview_result, readme_result])

    assert "README.md" in "\n".join(result.references)
    assert result.output["summaries"] == ["repo overview", "readme summary"]


def test_aggregation_executor_collects_completed_node_results():
    run = WorkflowRun(goal="demo")
    run.shared_state["node_results"] = {
        "repository_overview": NodeResult(status=NODE_STATUS_COMPLETED, output={"summary": "overview"}, references=["src/"]),
        "file_read": NodeResult(status=NODE_STATUS_COMPLETED, output={"summary": "readme"}, references=["README.md"]),
    }
    node = WorkflowNode(node_id="aggregate_results", node_type="aggregate_results")

    result = AggregationExecutor().execute(node, run, {})

    assert result.output["summaries"] == ["overview", "readme"]
    assert set(result.references) == {"src/", "README.md"}


def test_final_response_prefers_model_formatted_answer_when_available():
    run = WorkflowRun(goal="总结仓库情况")
    run.shared_state["aggregated_result"] = NodeResult(
        status=NODE_STATUS_COMPLETED,
        output={"summaries": ["overview", "readme"]},
        references=["src/", "README.md"],
    )
    node = WorkflowNode(node_id="final_response", node_type="final_response")
    client = _FakeChatClient("仓库包含源码目录，README 说明了项目用途。")

    result = FinalResponseExecutor().execute(
        node,
        run,
        {"application_context": SimpleNamespace(llm_client=client, llm_model="demo-model", services={})},
    )

    assert result.output["final_response"] == "仓库包含源码目录，README 说明了项目用途。"
    assert client.requests
