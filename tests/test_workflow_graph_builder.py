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
from agent_runtime_framework.workflow.decomposition import decompose_goal
from agent_runtime_framework.workflow.goal_analysis import analyze_goal
from agent_runtime_framework.workflow.graph_builder import compile_compat_workflow_graph
from agent_runtime_framework.workflow.models import GoalEnvelope, GoalSpec, new_agent_graph_state


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

    graph = compile_compat_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == ["conversation_response"]


def test_graph_builder_creates_small_graph_for_simple_file_read_request():
    goal = GoalSpec(
        original_goal="读取 README.md",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    graph = compile_compat_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == [
        "content_search",
        "chunked_file_read",
        "evidence_synthesis",
        "final_response",
    ]
    assert [(edge.source, edge.target) for edge in graph.edges] == [
        ("content_search", "chunked_file_read"),
        ("chunked_file_read", "evidence_synthesis"),
        ("evidence_synthesis", "final_response"),
    ]


def test_graph_builder_adds_aggregate_and_final_nodes_for_compound_goal():
    graph = compile_compat_workflow_graph(example_compound_goal())

    assert any(node.node_type == "aggregate_results" for node in graph.nodes)
    assert any(node.node_type == "final_response" for node in graph.nodes)


def test_graph_builder_builds_multiple_nodes_and_edges_for_compound_read_list_request():
    graph = compile_compat_workflow_graph(example_compound_goal())

    assert {node.node_id for node in graph.nodes} >= {
        "workspace_discovery",
        "content_search",
        "chunked_file_read",
        "aggregate_results",
        "evidence_synthesis",
        "final_response",
    }
    assert {(edge.source, edge.target) for edge in graph.edges} >= {
        ("workspace_discovery", "aggregate_results"),
        ("content_search", "chunked_file_read"),
        ("chunked_file_read", "aggregate_results"),
        ("aggregate_results", "evidence_synthesis"),
        ("evidence_synthesis", "final_response"),
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

    graph = compile_compat_workflow_graph(goal)

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

    graph = compile_compat_workflow_graph(goal)

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

    graph = compile_compat_workflow_graph(goal, context=context)

    assert graph.metadata["source"] == "model"


def test_graph_builder_rejects_fallback_when_model_only_and_model_missing():
    goal = GoalSpec(
        original_goal="demo",
        primary_intent="change_and_verify",
        metadata={"requires_verification": True},
    )
    context = SimpleNamespace(application_context=SimpleNamespace(llm_client=None, llm_model=None, services={}), services={"workflow_graph_model_only": True})

    with pytest.raises(ValueError, match="model-only"):
        compile_compat_workflow_graph(goal, context=context)


def test_graph_builder_prefers_model_output_when_available():
    context = _workflow_context(
        '{"nodes":[{"node_id":"file_read","node_type":"chunked_file_read","task_profile":"chunked_file_read",'
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

    graph = compile_compat_workflow_graph(goal, context=context)

    assert [node.node_id for node in graph.nodes] == ["file_read", "final_response"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [("file_read", "final_response")]


def test_graph_builder_accepts_model_defined_workspace_discovery_node():
    context = _workflow_context(
        '{"nodes":[{"node_id":"discover","node_type":"workspace_discovery","task_profile":"workspace_discovery",'
        '"dependencies":[],"metadata":{"workspace_root":"."}},'
        '{"node_id":"final_response","node_type":"final_response","dependencies":["discover"]}],'
        '"edges":[{"source":"discover","target":"final_response"}]}'
    )
    goal = GoalSpec(
        original_goal="列一下当前工作区都有什么文件",
        primary_intent="repository_overview",
        requires_repository_overview=True,
    )

    graph = compile_compat_workflow_graph(goal, context=context)

    assert [node.node_type for node in graph.nodes] == ["workspace_discovery", "final_response"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [("discover", "final_response")]


def test_graph_builder_accepts_model_defined_content_search_node():
    context = _workflow_context(
        '{"nodes":[{"node_id":"search","node_type":"content_search","task_profile":"content_search",'
        '"dependencies":[],"metadata":{"target_hint":"README.md"}},'
        '{"node_id":"final_response","node_type":"final_response","dependencies":["search"]}],'
        '"edges":[{"source":"search","target":"final_response"}]}'
    )
    goal = GoalSpec(
        original_goal="解释 README.md",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    graph = compile_compat_workflow_graph(goal, context=context)

    assert [node.node_type for node in graph.nodes] == ["content_search", "final_response"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [("search", "final_response")]


def test_analyze_goal_accepts_dict_context_with_application_context():
    context = _workflow_context(
        '{"primary_intent":"file_read","requires_repository_overview":false,'
        '"requires_file_read":true,"requires_final_synthesis":false,'
        '"target_paths":["README.md"],"metadata":{}}'
    )

    goal = analyze_goal(
        "读取 README.md",
        context={"application_context": context.application_context, "services": context.services},
    )

    assert goal.primary_intent == "file_read"
    assert goal.target_paths == ["README.md"]


def test_decompose_goal_accepts_dict_context_with_application_context():
    context = _workflow_context(
        '{"subtasks":[{"task_id":"content_search","task_profile":"content_search","target":"README.md","depends_on":[],"metadata":{}},'
        '{"task_id":"chunked_file_read","task_profile":"chunked_file_read","target":"README.md","depends_on":["content_search"],"metadata":{}}]}'
    )
    goal = GoalSpec(
        original_goal="读取 README.md",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    subtasks = decompose_goal(
        goal,
        context={"application_context": context.application_context, "services": context.services},
    )

    assert [subtask.task_profile for subtask in subtasks] == ["content_search", "chunked_file_read"]


def test_compile_compat_workflow_graph_accepts_dict_context_with_application_context():
    context = _workflow_context(
        '{"nodes":[{"node_id":"read","node_type":"chunked_file_read","task_profile":"chunked_file_read","dependencies":[],"metadata":{"target_path":"README.md"}},'
        '{"node_id":"final_response","node_type":"final_response","dependencies":["read"]}],'
        '"edges":[{"source":"read","target":"final_response"}]}'
    )
    goal = GoalSpec(
        original_goal="读取 README.md",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    graph = compile_compat_workflow_graph(
        goal,
        context={"application_context": context.application_context, "services": context.services},
    )

    assert [node.node_type for node in graph.nodes] == ["chunked_file_read", "final_response"]


def test_goal_analysis_records_strategy_and_fallback_reason():
    app_context = SimpleNamespace(services={}, llm_client=None, llm_model="")

    goal = analyze_goal("读取 README.md", context={"application_context": app_context, "services": {}})

    assert goal.primary_intent == "file_read"
    assert goal.metadata["strategy"] == "fallback"
    assert goal.metadata["model_role"] == "planner"
    assert goal.metadata["fallback_reason"] == "model unavailable"


def test_graph_builder_records_strategy_and_fallback_reason():
    goal = GoalSpec(
        original_goal="读取 README.md",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )
    app_context = SimpleNamespace(services={}, llm_client=None, llm_model="")

    graph = compile_compat_workflow_graph(goal, context={"application_context": app_context, "services": {}})

    assert graph.metadata["strategy"] == "fallback"
    assert graph.metadata["model_role"] == "planner"
    assert graph.metadata["fallback_reason"] == "model unavailable"


def test_planner_records_model_strategy_on_success_and_fallback_on_failure(monkeypatch):
    from agent_runtime_framework.workflow import planner_v2

    goal = GoalEnvelope(
        goal="解释 README.md",
        normalized_goal="解释 README.md",
        intent="target_explainer",
        target_hints=["README.md"],
        success_criteria=["produce a grounded response"],
    )
    state = new_agent_graph_state(run_id="run-strategy-1", goal_envelope=goal)

    monkeypatch.setattr(
        planner_v2,
        "_call_model_planner",
        lambda *args, **kwargs: {
            "nodes": [
                {
                    "node_id": "resolve",
                    "node_type": "target_resolution",
                    "reason": "Resolve target",
                    "inputs": {"query": "解释 README.md"},
                    "depends_on": [],
                    "success_criteria": ["resolve target"],
                }
            ],
            "planner_summary": "Model plan",
        },
    )

    modeled = planner_v2.plan_next_subgraph(goal, state, context=None)

    monkeypatch.setattr(
        planner_v2,
        "_plan_next_subgraph_with_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("planner offline")),
    )
    fallback = planner_v2.plan_next_subgraph(goal, state, context=None)

    assert modeled.metadata["strategy"] == "model"
    assert modeled.metadata["model_role"] == "planner"
    assert fallback.metadata["strategy"] == "fallback"
    assert fallback.metadata["fallback_reason"] == "planner offline"


def test_workflow_llm_access_resolves_application_context_from_dict():
    from agent_runtime_framework.workflow.llm_access import get_application_context

    application_context = SimpleNamespace(name="app")

    assert get_application_context({"application_context": application_context}) is application_context


def test_workflow_llm_access_returns_none_when_model_is_unavailable():
    from agent_runtime_framework.workflow.llm_access import resolve_workflow_model_runtime

    application_context = SimpleNamespace(services={}, llm_client=None, llm_model="")

    assert resolve_workflow_model_runtime({"application_context": application_context}, "planner") is None


def test_planner_v2_emits_whitelisted_nodes_with_reason_and_success_criteria():
    from agent_runtime_framework.workflow.planner_v2 import ALLOWED_DYNAMIC_NODE_TYPES, plan_next_subgraph

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        success_criteria=["collect README evidence"],
    )
    state = new_agent_graph_state(run_id="run-1", goal_envelope=goal)

    subgraph = plan_next_subgraph(goal, state, context=None)

    assert 1 <= len(subgraph.nodes) <= 3
    assert all(node.node_type in ALLOWED_DYNAMIC_NODE_TYPES for node in subgraph.nodes)
    assert all(node.reason for node in subgraph.nodes)
    assert all(node.success_criteria for node in subgraph.nodes)
    assert all(node.node_type != "final_response" for node in subgraph.nodes)


def test_planner_v2_respects_max_dynamic_nodes_constraint():
    from agent_runtime_framework.workflow.planner_v2 import plan_next_subgraph

    goal = GoalEnvelope(
        goal="总结 docs 并读取 README.md",
        normalized_goal="总结 docs 并读取 README.md",
        intent="compound",
        target_hints=["README.md", "docs"],
        constraints={"max_dynamic_nodes": 2},
        success_criteria=["collect workspace and file evidence"],
    )
    state = new_agent_graph_state(run_id="run-2", goal_envelope=goal)

    subgraph = plan_next_subgraph(goal, state, context=None)

    assert len(subgraph.nodes) <= 2


def test_deterministic_planner_builds_file_read_subgraph():
    from agent_runtime_framework.workflow.planner_v2 import _plan_next_subgraph_deterministically

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        success_criteria=["collect README evidence"],
    )
    state = new_agent_graph_state(run_id="run-det-1", goal_envelope=goal)

    subgraph = _plan_next_subgraph_deterministically(goal, state, context=None)

    assert [node.node_type for node in subgraph.nodes] == [
        "content_search",
        "chunked_file_read",
        "evidence_synthesis",
    ]
    assert subgraph.metadata["planner"] == "deterministic_v2"


def test_model_planner_uses_valid_json_draft(monkeypatch):
    from agent_runtime_framework.workflow import planner_v2

    goal = GoalEnvelope(
        goal="解释 README.md",
        normalized_goal="解释 README.md",
        intent="target_explainer",
        target_hints=["README.md"],
        success_criteria=["produce a grounded response"],
    )
    state = new_agent_graph_state(run_id="run-model-1", goal_envelope=goal)

    monkeypatch.setattr(
        planner_v2,
        "_call_model_planner",
        lambda *args, **kwargs: {
            "nodes": [
                {
                    "node_id": "resolve",
                    "node_type": "target_resolution",
                    "reason": "Resolve target",
                    "inputs": {"query": "解释 README.md"},
                    "depends_on": [],
                    "success_criteria": ["resolve target"],
                },
                {
                    "node_id": "read",
                    "node_type": "chunked_file_read",
                    "reason": "Read target",
                    "inputs": {"target_path": "README.md"},
                    "depends_on": ["resolve"],
                    "success_criteria": ["read target"],
                },
            ],
            "planner_summary": "Model plan for target_explainer",
        },
    )

    subgraph = planner_v2._plan_next_subgraph_with_model(goal, state, context=None)

    assert [node.node_type for node in subgraph.nodes] == ["target_resolution", "chunked_file_read"]
    assert subgraph.metadata["planner"] == "model_v1"


def test_model_planner_accepts_dict_context_with_application_context(monkeypatch):
    from types import SimpleNamespace

    from agent_runtime_framework.workflow import planner_v2

    goal = GoalEnvelope(
        goal="解释 README.md",
        normalized_goal="解释 README.md",
        intent="target_explainer",
        target_hints=["README.md"],
        success_criteria=["produce a grounded response"],
    )
    state = new_agent_graph_state(run_id="run-model-ctx-1", goal_envelope=goal)
    application_context = SimpleNamespace(llm_client=object(), llm_model="demo-model")

    monkeypatch.setattr(
        planner_v2,
        "resolve_model_runtime",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        planner_v2,
        "chat_once",
        lambda *args, **kwargs: SimpleNamespace(
            content='{"planner_summary":"dict context plan","nodes":[{"node_id":"resolve","node_type":"target_resolution","reason":"Resolve target","inputs":{"query":"解释 README.md"},"depends_on":[],"success_criteria":["resolve target"]}]}'
        ),
    )

    subgraph = planner_v2._plan_next_subgraph_with_model(
        goal,
        state,
        context={"application_context": application_context},
    )

    assert [node.node_type for node in subgraph.nodes] == ["target_resolution"]
    assert subgraph.metadata["planner"] == "model_v1"


def test_plan_next_subgraph_falls_back_when_model_returns_invalid_node_type(monkeypatch):
    from agent_runtime_framework.workflow import planner_v2

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        success_criteria=["collect README evidence"],
    )
    state = new_agent_graph_state(run_id="run-fallback-1", goal_envelope=goal)

    monkeypatch.setattr(
        planner_v2,
        "_plan_next_subgraph_with_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad node")),
    )

    subgraph = planner_v2.plan_next_subgraph(goal, state, context=None)

    assert [node.node_type for node in subgraph.nodes] == [
        "content_search",
        "chunked_file_read",
        "evidence_synthesis",
    ]
    assert subgraph.metadata["planner"] == "deterministic_v2"
    assert subgraph.metadata["fallback_reason"] == "bad node"


def test_plan_next_subgraph_falls_back_when_model_is_unavailable(monkeypatch):
    from agent_runtime_framework.workflow import planner_v2

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        success_criteria=["collect README evidence"],
    )
    state = new_agent_graph_state(run_id="run-fallback-2", goal_envelope=goal)

    monkeypatch.setattr(
        planner_v2,
        "_plan_next_subgraph_with_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("model planner unavailable")),
    )

    subgraph = planner_v2.plan_next_subgraph(goal, state, context=None)

    assert [node.node_type for node in subgraph.nodes] == [
        "content_search",
        "chunked_file_read",
        "evidence_synthesis",
    ]
    assert subgraph.metadata["planner"] == "deterministic_v2"
    assert subgraph.metadata["fallback_reason"] == "model planner unavailable"


def test_plan_next_subgraph_skips_model_when_config_disables_it():
    from agent_runtime_framework.workflow.planner_v2 import plan_next_subgraph

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        constraints={"planner_mode": "deterministic"},
        success_criteria=["collect README evidence"],
    )
    state = new_agent_graph_state(run_id="run-gated-1", goal_envelope=goal)

    subgraph = plan_next_subgraph(goal, state, context=None)

    assert subgraph.metadata["planner"] == "deterministic_v2"
    assert "fallback_reason" not in subgraph.metadata


def test_graph_builder_accepts_model_defined_chunked_file_read_node():
    context = _workflow_context(
        '{"nodes":[{"node_id":"read","node_type":"chunked_file_read","task_profile":"chunked_file_read",'
        '"dependencies":[],"metadata":{"target_path":"README.md"}},'
        '{"node_id":"final_response","node_type":"final_response","dependencies":["read"]}],'
        '"edges":[{"source":"read","target":"final_response"}]}'
    )
    goal = GoalSpec(
        original_goal="读取 README.md",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    graph = compile_compat_workflow_graph(goal, context=context)

    assert [node.node_type for node in graph.nodes] == ["chunked_file_read", "final_response"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [("read", "final_response")]


def test_graph_builder_uses_model_without_feature_flag():
    context = _workflow_context(
        '{"nodes":[{"node_id":"file_read","node_type":"chunked_file_read","task_profile":"chunked_file_read",'
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

    graph = compile_compat_workflow_graph(goal, context=context)

    assert [node.node_id for node in graph.nodes] == ["file_read", "final_response"]


def test_graph_builder_falls_back_to_workspace_subtask_for_non_native_goal():
    goal = GoalSpec(
        original_goal="编辑 README.md 并验证修改结果",
        primary_intent="change_and_verify",
    )

    graph = compile_compat_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == ["workspace_subtask", "final_response"]
    assert graph.nodes[0].metadata["goal"] == "编辑 README.md 并验证修改结果"



def test_graph_builder_creates_native_graph_for_repository_overview_request():
    goal = GoalSpec(
        original_goal="列一下当前工作区都有什么文件",
        primary_intent="repository_overview",
        requires_repository_overview=True,
    )

    graph = compile_compat_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == ["workspace_discovery", "evidence_synthesis", "final_response"]
    assert all(node.node_type != "workspace_subtask" for node in graph.nodes)



def test_graph_builder_keeps_compound_read_and_summarize_request_native():
    graph = compile_compat_workflow_graph(example_compound_goal())

    assert all(node.node_type != "workspace_subtask" for node in graph.nodes)
    assert {node.node_type for node in graph.nodes} >= {
        "workspace_discovery",
        "content_search",
        "chunked_file_read",
        "aggregate_results",
        "evidence_synthesis",
        "final_response",
    }


def test_graph_builder_no_longer_emits_legacy_native_nodes_by_default():
    graph = compile_compat_workflow_graph(example_compound_goal())

    node_types = {node.node_type for node in graph.nodes}

    assert "repository_explainer" not in node_types
    assert "file_reader" not in node_types



def test_graph_builder_records_fallback_reason_for_non_native_goal():
    goal = GoalSpec(
        original_goal="编辑 README.md 并验证修改结果",
        primary_intent="change_and_verify",
    )

    graph = compile_compat_workflow_graph(goal)

    assert graph.metadata["execution_mode"] == "mixed"
    assert "unsupported_primary_intent" in graph.metadata["fallback_reasons"]
    assert graph.nodes[0].metadata["fallback_reason"] == "unsupported_primary_intent"


def test_graph_builder_marks_compatibility_mode_for_legacy_entrypoint():
    goal = GoalSpec(
        original_goal="读取 README.md",
        primary_intent="file_read",
        requires_file_read=True,
        target_paths=["README.md"],
    )

    graph = compile_compat_workflow_graph(goal)

    assert graph.metadata["compatibility_mode"] is True
    assert graph.metadata["compatibility_entrypoint"] == "compile_compat_workflow_graph"



def test_graph_builder_inserts_approval_gate_for_high_risk_workspace_subtask_goal():
    goal = GoalSpec(
        original_goal="直接删除 README.md",
        primary_intent="dangerous_change",
        metadata={"requires_approval": True},
    )

    graph = compile_compat_workflow_graph(goal)

    assert any(node.node_type == "workspace_subtask" for node in graph.nodes)
    assert any(node.node_type == "approval_gate" for node in graph.nodes)
    assert graph.metadata["execution_mode"] == "mixed"



def test_graph_builder_creates_target_explainer_graph_for_module_question():
    goal = GoalSpec(
        original_goal="请讲解 service 这个模块在做什么",
        primary_intent="target_explainer",
        metadata={"target_query": "请讲解 service 这个模块在做什么"},
    )

    graph = compile_compat_workflow_graph(goal)

    assert [node.node_type for node in graph.nodes] == [
        "target_resolution",
        "content_search",
        "chunked_file_read",
        "evidence_synthesis",
        "final_response",
    ]
    assert graph.metadata["execution_mode"] == "native"
