from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.memory import InMemorySessionMemory
from agent_runtime_framework.models import DriverCapabilities, InMemoryCredentialStore, ModelProfile, ModelRegistry, ModelRouter
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.workflow.decomposition import decompose_goal
from agent_runtime_framework.workflow.goal_analysis import analyze_goal
from agent_runtime_framework.workflow.models import GoalSpec, JudgeDecision, SubTaskSpec, new_agent_graph_state
from agent_runtime_framework.workflow.subgraph_planner import _planner_context_payload, plan_next_subgraph


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))])


class _FakeLLMClient:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


@dataclass
class _FakeInstance:
    instance_id: str = "fake"
    client_content: str = '{"primary_intent":"file_read"}'
    last_client: _FakeLLMClient | None = None
    capabilities: DriverCapabilities = field(default_factory=DriverCapabilities)

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

    def list_models(self) -> list[ModelProfile]:
        return list(self._profiles)

    def authenticate(self, credentials: dict[str, str], store: InMemoryCredentialStore):
        store.set(self.instance_id, credentials)
        return SimpleNamespace(instance=self.instance_id, authenticated=True, auth_type="api_key", error_message=None)

    def get_client(self, store: InMemoryCredentialStore):
        self.last_client = _FakeLLMClient(self.client_content)
        return self.last_client


def _workflow_context(model_payload: str):
    workspace = LocalFileResourceRepository(["."])
    app_context = ApplicationContext(
        resource_repository=workspace,
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
    return SimpleNamespace(
        application_context=app_context,
        services={},
    )


def test_analyze_goal_prefers_model_output_when_available():
    context = _workflow_context(
        '{"primary_intent":"compound","requires_repository_overview":true,"requires_file_read":true,'
        '"requires_final_synthesis":true,"target_paths":["README.md"]}'
    )

    goal = analyze_goal("随便一句话", context=context)

    assert goal.primary_intent == "compound"
    assert goal.requires_repository_overview is True
    assert goal.requires_file_read is True
    assert goal.target_paths == ["README.md"]


def test_decompose_goal_prefers_model_output_when_available():
    context = _workflow_context(
        '{"subtasks":[{"task_id":"workspace_discovery","task_profile":"workspace_discovery","target":"."},'
        '{"task_id":"content_search","task_profile":"content_search","target":"README.md"},'
        '{"task_id":"chunked_file_read","task_profile":"chunked_file_read","target":"README.md","depends_on":["content_search"]},'
        '{"task_id":"evidence_synthesis","task_profile":"evidence_synthesis","depends_on":["workspace_discovery","content_search","chunked_file_read"]}]}'
    )
    goal = GoalSpec(
        original_goal="demo",
        primary_intent="compound",
        requires_repository_overview=True,
        requires_file_read=True,
        requires_final_synthesis=True,
        target_paths=["README.md"],
    )

    subtasks = decompose_goal(goal, context=context)

    assert subtasks == [
        SubTaskSpec(task_id="workspace_discovery", task_profile="workspace_discovery", target="."),
        SubTaskSpec(task_id="content_search", task_profile="content_search", target="README.md"),
        SubTaskSpec(task_id="chunked_file_read", task_profile="chunked_file_read", target="README.md", depends_on=["content_search"]),
        SubTaskSpec(task_id="evidence_synthesis", task_profile="evidence_synthesis", depends_on=["workspace_discovery", "content_search", "chunked_file_read"]),
    ]


def test_plan_next_subgraph_model_payload_includes_latest_judge_feedback():
    instance = _FakeInstance(
        client_content='{"planner_summary":"verification follow-up","nodes":[{"node_id":"verify","node_type":"verification","reason":"judge requested verification","inputs":{"verification_type":"post_write"},"depends_on":[],"success_criteria":["produce verification result"]}]}'
    )
    workspace = LocalFileResourceRepository(["."])
    app_context = ApplicationContext(
        resource_repository=workspace,
        session_memory=InMemorySessionMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": "."},
    )
    registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    registry.register_instance(instance)
    registry.authenticate("fake", {"api_key": "secret"})
    router = ModelRouter(registry)
    router.set_route("planner", instance_id="fake", model_name="planner-model")
    app_context.services["model_registry"] = registry
    app_context.services["model_router"] = router
    context = SimpleNamespace(application_context=app_context, services={})

    envelope = SimpleNamespace(
        goal="创建 tet.txt 并写入内容",
        normalized_goal="创建 tet.txt 并写入内容",
        intent="change_and_verify",
        target_hints=["tet.txt"],
        success_criteria=["write file"],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-feedback-2", goal_envelope=envelope)
    state.aggregated_payload["summaries"] = ["created tet.txt"]
    state.aggregated_payload["evidence_items"] = [{"kind": "path", "path": "tet.txt"}]
    state.open_issues = ["verification"]
    state.attempted_strategies = ["create file", "summarize write result"]
    state.failure_history.append(
        {
            "iteration": 1,
            "status": "needs_verification",
            "reason": "Verification coverage is missing",
            "missing_evidence": ["verification"],
            "diagnosis": {"primary_gap": "verification_missing"},
            "strategy_guidance": {"recommended_strategy": "verify_existing_changes"},
        }
    )
    state.judge_history.append(
        JudgeDecision(
            status="needs_verification",
            reason="Verification coverage is missing",
            missing_evidence=["verification"],
            replan_hint={"next_node_type": "verification", "verification_type": "post_write"},
            diagnosis={"primary_gap": "verification_missing"},
            strategy_guidance={"recommended_strategy": "verify_existing_changes"},
        )
    )

    subgraph = plan_next_subgraph(envelope, state, context=context)

    assert subgraph.nodes[0].node_type == "verification"
    client = instance.last_client
    assert client is not None
    request_body = client.completions.last_kwargs["messages"][1]["content"]
    assert '"latest_judge_decision"' in request_body
    assert '"needs_verification"' in request_body
    assert '"execution_summary"' in request_body
    assert '"open_issues"' in request_body
    assert '"attempted_strategies"' in request_body
    assert '"failure_history"' in request_body
    assert '"verification_missing"' in request_body


def test_plan_next_subgraph_uses_model_even_when_context_requests_deterministic_mode():
    context = _workflow_context(
        '{"planner_summary":"model plan","nodes":[{"node_id":"verify","node_type":"verification","reason":"model picked verification","inputs":{"verification_type":"post_write"},"depends_on":[],"success_criteria":["produce verification result"]}]}'
    )
    envelope = SimpleNamespace(
        goal="创建 tet.txt 并写入内容",
        normalized_goal="创建 tet.txt 并写入内容",
        intent="change_and_verify",
        target_hints=["tet.txt"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-model-first", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=SimpleNamespace(application_context=context.application_context, services={}))

    assert subgraph.nodes[0].node_type == "verification"


def test_plan_next_subgraph_accepts_semantic_planning_node_types():
    context = _workflow_context(
        '{"planner_summary":"interpret then search","nodes":[{"node_id":"interpret","node_type":"interpret_target","reason":"resolve target semantics","inputs":{},"depends_on":[],"success_criteria":["capture target constraints"]},{"node_id":"search_plan","node_type":"plan_search","reason":"plan search strategy","inputs":{},"depends_on":["interpret"],"success_criteria":["define search plan"]},{"node_id":"read_plan","node_type":"plan_read","reason":"plan reading strategy","inputs":{},"depends_on":["search_plan"],"success_criteria":["define read plan"]}]}'
    )
    envelope = SimpleNamespace(
        goal="看根目录 README",
        normalized_goal="看根目录 README",
        intent="file_read",
        target_hints=["README"],
        success_criteria=["read target"],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-semantic-nodes", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=context)

    assert [node.node_type for node in subgraph.nodes] == ["interpret_target", "plan_search", "plan_read"]


def test_plan_next_subgraph_ignores_non_mapping_inputs_payload():
    context = _workflow_context(
        '{"planner_summary":"model plan","nodes":[{"node_id":"verify","node_type":"verification","reason":"model picked verification","inputs":["post_write_input"],"depends_on":[],"success_criteria":["produce verification result"]}]}'
    )
    envelope = SimpleNamespace(
        goal="创建 tet.txt 并写入内容",
        normalized_goal="创建 tet.txt 并写入内容",
        intent="change_and_verify",
        target_hints=["tet.txt"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-model-invalid-inputs", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=context)

    assert subgraph.nodes[0].node_type == "verification"
    assert subgraph.nodes[0].inputs == {}


def test_planner_context_payload_compacts_histories_and_surfaces_ineffective_actions():
    envelope = SimpleNamespace(
        goal="解释 service 模块",
        normalized_goal="解释 service 模块",
        intent="compound",
        target_hints=["service"],
        success_criteria=["ground answer"],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-compact", goal_envelope=envelope)
    state.current_iteration = 4
    state.open_issues = ["grounded evidence", "resolved conflict"]
    state.attempted_strategies = [
        "workspace scan",
        "search service",
        "read service file",
        "summarize candidate",
        "search service",
        "compare entrypoints",
    ]
    state.failure_history = [
        {
            "iteration": 1,
            "status": "needs_more_evidence",
            "reason": "missing grounded evidence",
            "missing_evidence": ["grounded evidence"],
            "diagnosis": {"primary_gap": "grounded_evidence_missing"},
            "strategy_guidance": {"recommended_strategy": "gather_grounded_evidence"},
        },
        {
            "iteration": 2,
            "status": "needs_more_evidence",
            "reason": "still missing grounded evidence",
            "missing_evidence": ["grounded evidence"],
            "diagnosis": {"primary_gap": "grounded_evidence_missing"},
            "strategy_guidance": {"recommended_strategy": "gather_grounded_evidence"},
        },
        {
            "iteration": 3,
            "status": "needs_more_evidence",
            "reason": "conflicting candidates",
            "missing_evidence": ["resolved conflict"],
            "diagnosis": {"primary_gap": "conflicting_evidence"},
            "strategy_guidance": {"recommended_strategy": "resolve_conflict_before_answering"},
        },
        {
            "iteration": 4,
            "status": "needs_verification",
            "reason": "verification missing",
            "missing_evidence": ["verification"],
            "diagnosis": {"primary_gap": "verification_missing"},
            "strategy_guidance": {"recommended_strategy": "verify_existing_changes"},
        },
    ]
    state.iteration_summaries = [
        {"iteration": 1, "planner_summary": "workspace scan", "judge_status": "needs_more_evidence"},
        {"iteration": 2, "planner_summary": "search service", "judge_status": "needs_more_evidence"},
        {"iteration": 3, "planner_summary": "compare entrypoints", "judge_status": "needs_more_evidence"},
        {"iteration": 4, "planner_summary": "verify answer", "judge_status": "needs_verification"},
    ]

    payload = _planner_context_payload(envelope, state)

    assert payload["iteration"] == 5
    assert payload["attempted_strategies"] == [
        "search service",
        "read service file",
        "summarize candidate",
        "compare entrypoints",
    ]
    assert len(payload["failure_history"]) == 2
    assert payload["failure_history"][0]["iteration"] == 3
    assert payload["failure_history"][-1]["diagnosis"]["primary_gap"] == "verification_missing"
    assert payload["ineffective_actions"] == [
        "compare entrypoints",
        "verify answer",
    ]
    assert len(payload["iteration_summaries"]) == 2


def test_plan_next_subgraph_request_body_uses_compacted_context():
    context = _workflow_context(
        '{"planner_summary":"model plan","nodes":[{"node_id":"verify","node_type":"verification","reason":"model picked verification","inputs":{"verification_type":"post_write"},"depends_on":[],"success_criteria":["produce verification result"]}]}'
    )
    envelope = SimpleNamespace(
        goal="创建 tet.txt 并写入内容",
        normalized_goal="创建 tet.txt 并写入内容",
        intent="change_and_verify",
        target_hints=["tet.txt"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-model-compact", goal_envelope=envelope)
    state.attempted_strategies = [
        "workspace scan",
        "search tet",
        "read tet",
        "summarize tet",
        "search tet",
        "verify tet",
    ]
    state.failure_history = [
        {"iteration": 1, "status": "needs_more_evidence", "reason": "a", "diagnosis": {"primary_gap": "grounded_evidence_missing"}},
        {"iteration": 2, "status": "needs_more_evidence", "reason": "b", "diagnosis": {"primary_gap": "grounded_evidence_missing"}},
        {"iteration": 3, "status": "needs_verification", "reason": "c", "diagnosis": {"primary_gap": "verification_missing"}},
        {"iteration": 4, "status": "needs_verification", "reason": "d", "diagnosis": {"primary_gap": "verification_missing"}},
    ]
    state.iteration_summaries = [
        {"iteration": 1, "planner_summary": "workspace scan", "judge_status": "needs_more_evidence"},
        {"iteration": 2, "planner_summary": "search tet", "judge_status": "needs_more_evidence"},
        {"iteration": 3, "planner_summary": "read tet", "judge_status": "needs_verification"},
        {"iteration": 4, "planner_summary": "verify tet", "judge_status": "needs_verification"},
    ]

    plan_next_subgraph(envelope, state, context=SimpleNamespace(application_context=context.application_context, services={}))

    instance = context.application_context.services["model_registry"].instance("fake")
    client = instance.last_client
    assert client is not None
    request_body = client.completions.last_kwargs["messages"][1]["content"]
    assert request_body.count('"iteration"') < 7
    assert '"ineffective_actions"' in request_body
    assert '"workspace scan"' not in request_body
