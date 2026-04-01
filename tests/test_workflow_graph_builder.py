from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.memory import InMemorySessionMemory
from agent_runtime_framework.models import DriverCapabilities, InMemoryCredentialStore, ModelProfile, ModelRegistry, ModelRouter
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.workflow.graph_builder import build_workflow_graph
from agent_runtime_framework.workflow.models import GoalSpec


@dataclass
class _FakeInstance:
    instance_id: str = "fake"
    client_content: str = '{"nodes":[],"edges":[]}'
    capabilities: DriverCapabilities = field(default_factory=DriverCapabilities)
    last_client: object | None = None

    def __post_init__(self) -> None:
        self._profiles = [
            ModelProfile(
                instance=self.instance_id,
                model_name="planner-model",
                display_name="Planner Model",
                supports_chat=True,
                cost_level="medium",
                latency_level="medium",
                reasoning_level="high",
                recommended_roles=["planner"],
            )
        ]

    def list_models(self):
        return list(self._profiles)

    def authenticate(self, credentials, store):
        store.set(self.instance_id, credentials)
        return SimpleNamespace(instance=self.instance_id, authenticated=True, auth_type="api_key", error_message=None)

    def get_client(self, store):
        completions = SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self.client_content))]
            )
        )
        self.last_client = SimpleNamespace(chat=SimpleNamespace(completions=completions), completions=completions)
        return self.last_client


def _workflow_context(model_payload: str):
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository(["."]),
        session_memory=InMemorySessionMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": "."},
    )
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_instance(_FakeInstance(client_content=model_payload))
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("planner", instance_id="fake", model_name="planner-model")
    app_context.services["model_registry"] = registry
    app_context.services["model_router"] = router
    return SimpleNamespace(application_context=app_context, services={})


def example_compound_goal() -> GoalSpec:
    return GoalSpec(
        original_goal="列目录并读取 README 后做总结",
        primary_intent="compound",
        requires_repository_overview=True,
        requires_file_read=True,
        requires_final_synthesis=True,
        target_paths=["README.md"],
    )


def test_graph_builder_creates_conversation_node_for_generic_goal():
    goal = GoalSpec(
        original_goal="你是谁？",
        primary_intent="generic",
    )

    graph = build_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == ["conversation_response"]


def test_graph_builder_creates_small_graph_for_simple_file_read_request():
    goal = GoalSpec(
        original_goal="读取 README.md",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    graph = build_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == ["file_reader", "final_response"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [("file_read", "final_response")]


def test_graph_builder_adds_aggregate_and_final_nodes_for_compound_goal():
    graph = build_workflow_graph(example_compound_goal())

    assert any(node.node_type == "aggregate_results" for node in graph.nodes)
    assert any(node.node_type == "final_response" for node in graph.nodes)


def test_graph_builder_builds_multiple_nodes_and_edges_for_compound_read_list_request():
    graph = build_workflow_graph(example_compound_goal())

    assert {node.node_id for node in graph.nodes} >= {
        "repository_overview",
        "file_read",
        "aggregate_results",
        "final_response",
    }
    assert {(edge.source, edge.target) for edge in graph.edges} >= {
        ("repository_overview", "aggregate_results"),
        ("file_read", "aggregate_results"),
        ("aggregate_results", "final_response"),
    }


def test_graph_builder_inserts_verification_node_for_change_flows():
    goal = GoalSpec(
        original_goal="修改 README.md 然后验证结果",
        primary_intent="change",
        requires_file_read=True,
        requires_final_synthesis=True,
        target_paths=["README.md"],
        metadata={"requires_verification": True},
    )

    graph = build_workflow_graph(goal)

    assert any(node.node_type == "verification" for node in graph.nodes)


def test_graph_builder_uses_codex_node_for_non_native_request():
    goal = GoalSpec(
        original_goal="把 README.md 改成更正式的文案",
        primary_intent="change_and_verify",
        requires_file_read=True,
        requires_final_synthesis=True,
        target_paths=["README.md"],
        metadata={"requires_verification": True},
    )

    graph = build_workflow_graph(goal)

    assert any(node.node_type == "workspace_subtask" for node in graph.nodes)
    assert any(node.node_type == "verification" for node in graph.nodes)
    assert any(node.node_type == "final_response" for node in graph.nodes)


def test_graph_builder_can_enforce_model_only_mode():
    context = _workflow_context('{"nodes":[{"node_id":"custom_plan","node_type":"workspace_subtask","task_profile":"change_and_verify","dependencies":[],"metadata":{"goal":"demo"}}],"edges":[]}')
    context.services["workflow_graph_model_only"] = True
    goal = GoalSpec(
        original_goal="demo",
        primary_intent="change_and_verify",
        metadata={"requires_verification": True},
    )

    graph = build_workflow_graph(goal, context=context)

    assert graph.metadata["source"] == "model"


def test_graph_builder_rejects_fallback_when_model_only_and_model_missing():
    goal = GoalSpec(
        original_goal="demo",
        primary_intent="change_and_verify",
        metadata={"requires_verification": True},
    )
    context = SimpleNamespace(application_context=SimpleNamespace(llm_client=None, llm_model=None, services={}), services={"workflow_graph_model_only": True})

    with pytest.raises(ValueError, match="model-only"):
        build_workflow_graph(goal, context=context)


def test_graph_builder_prefers_model_output_when_available():
    context = _workflow_context(
        '{"nodes":[{"node_id":"file_read","node_type":"file_reader","task_profile":"file_reader",'
        '"dependencies":[],"metadata":{"target_path":"README.md"}},'
        '{"node_id":"final_response","node_type":"final_response","dependencies":["file_read"]}],'
        '"edges":[{"source":"file_read","target":"final_response"}]}'
    )
    goal = GoalSpec(
        original_goal="demo",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    graph = build_workflow_graph(goal, context=context)

    assert [node.node_id for node in graph.nodes] == ["file_read", "final_response"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [("file_read", "final_response")]


def test_graph_builder_uses_model_without_feature_flag():
    context = _workflow_context(
        '{"nodes":[{"node_id":"file_read","node_type":"file_reader","task_profile":"file_reader",'
        '"dependencies":[],"metadata":{"target_path":"README.md"}},'
        '{"node_id":"final_response","node_type":"final_response","dependencies":["file_read"]}],'
        '"edges":[{"source":"file_read","target":"final_response"}]}'
    )
    goal = GoalSpec(
        original_goal="demo",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    graph = build_workflow_graph(goal, context=context)

    assert [node.node_id for node in graph.nodes] == ["file_read", "final_response"]


def test_graph_builder_falls_back_to_workspace_subtask_for_non_native_goal():
    goal = GoalSpec(
        original_goal="编辑 README.md 并验证修改结果",
        primary_intent="change_and_verify",
    )

    graph = build_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == ["workspace_subtask", "final_response"]
    assert graph.nodes[0].metadata["goal"] == "编辑 README.md 并验证修改结果"



def test_graph_builder_creates_native_graph_for_repository_overview_request():
    goal = GoalSpec(
        original_goal="列一下当前工作区都有什么文件",
        primary_intent="repository_overview",
        requires_repository_overview=True,
    )

    graph = build_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == ["repository_explainer", "final_response"]
    assert all(node.node_type != "workspace_subtask" for node in graph.nodes)



def test_graph_builder_keeps_compound_read_and_summarize_request_native():
    graph = build_workflow_graph(example_compound_goal())

    assert all(node.node_type != "workspace_subtask" for node in graph.nodes)
    assert {node.node_type for node in graph.nodes} >= {"repository_explainer", "file_reader", "aggregate_results", "final_response"}



def test_graph_builder_records_fallback_reason_for_non_native_goal():
    goal = GoalSpec(
        original_goal="编辑 README.md 并验证修改结果",
        primary_intent="change_and_verify",
    )

    graph = build_workflow_graph(goal)

    assert graph.metadata["execution_mode"] == "mixed"
    assert "unsupported_primary_intent" in graph.metadata["fallback_reasons"]
    assert graph.nodes[0].metadata["fallback_reason"] == "unsupported_primary_intent"



def test_graph_builder_inserts_approval_gate_for_high_risk_workspace_subtask_goal():
    goal = GoalSpec(
        original_goal="直接删除 README.md",
        primary_intent="dangerous_change",
        metadata={"requires_approval": True},
    )

    graph = build_workflow_graph(goal)

    assert any(node.node_type == "workspace_subtask" for node in graph.nodes)
    assert any(node.node_type == "approval_gate" for node in graph.nodes)
    assert graph.metadata["execution_mode"] == "mixed"



def test_graph_builder_creates_target_explainer_graph_for_module_question():
    goal = GoalSpec(
        original_goal="请讲解 service 这个模块在做什么",
        primary_intent="target_explainer",
        metadata={"target_query": "请讲解 service 这个模块在做什么"},
    )

    graph = build_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == [
        "target_resolution",
        "file_inspection",
        "response_synthesis",
        "final_response",
    ]
    assert graph.metadata["execution_mode"] == "native"
