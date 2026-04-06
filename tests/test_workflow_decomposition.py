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
from agent_runtime_framework.workflow.subgraph_planner import plan_next_subgraph


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


def test_simple_file_read_request_becomes_single_subtask():
    goal = analyze_goal("请读取 README.md 并总结内容")

    subtasks = decompose_goal(goal)

    assert goal.primary_intent == "file_read"
    assert subtasks == [
        SubTaskSpec(task_id="content_search", task_profile="content_search", target="README.md", metadata={"strategy": "deterministic", "model_role": "planner"}),
        SubTaskSpec(task_id="chunked_file_read", task_profile="chunked_file_read", target="README.md", depends_on=["content_search"], metadata={"strategy": "deterministic", "model_role": "planner"}),
    ]


def test_compound_request_decomposes_into_multiple_subtasks():
    goal = analyze_goal("帮我列一下当前文件夹都有什么，以及读取一下README文件并总结告诉我在讲什么")

    subtasks = decompose_goal(goal)

    assert [item.task_profile for item in subtasks] == [
        "workspace_discovery",
        "content_search",
        "chunked_file_read",
        "evidence_synthesis",
    ]


def test_directory_and_readme_request_decomposes_into_overview_file_read_and_synthesis():
    goal = GoalSpec(
        original_goal="介绍一下这个仓库结构，再读 README.md 做总结",
        primary_intent="compound",
        requires_repository_overview=True,
        requires_file_read=True,
        requires_final_synthesis=True,
        target_paths=["README.md"],
    )

    subtasks = decompose_goal(goal)

    assert subtasks == [
        SubTaskSpec(task_id="workspace_discovery", task_profile="workspace_discovery", target=".", metadata={"strategy": "deterministic", "model_role": "planner"}),
        SubTaskSpec(task_id="content_search", task_profile="content_search", target="README.md", metadata={"strategy": "deterministic", "model_role": "planner"}),
        SubTaskSpec(task_id="chunked_file_read", task_profile="chunked_file_read", target="README.md", depends_on=["content_search"], metadata={"strategy": "deterministic", "model_role": "planner"}),
        SubTaskSpec(task_id="evidence_synthesis", task_profile="evidence_synthesis", depends_on=["workspace_discovery", "content_search", "chunked_file_read"], metadata={"strategy": "deterministic", "model_role": "planner"}),
    ]


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


def test_analyze_goal_uses_model_without_feature_flag():
    context = _workflow_context('{"primary_intent":"repository_overview","requires_repository_overview":true}')

    goal = analyze_goal("随便一句话", context=context)

    assert goal.primary_intent == "repository_overview"
    assert goal.requires_repository_overview is True


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
        SubTaskSpec(task_id="workspace_discovery", task_profile="workspace_discovery", target=".", metadata={"strategy": "model", "model_role": "planner"}),
        SubTaskSpec(task_id="content_search", task_profile="content_search", target="README.md", metadata={"strategy": "model", "model_role": "planner"}),
        SubTaskSpec(task_id="chunked_file_read", task_profile="chunked_file_read", target="README.md", depends_on=["content_search"], metadata={"strategy": "model", "model_role": "planner"}),
        SubTaskSpec(task_id="evidence_synthesis", task_profile="evidence_synthesis", depends_on=["workspace_discovery", "content_search", "chunked_file_read"], metadata={"strategy": "model", "model_role": "planner"}),
    ]


def test_decompose_goal_uses_model_without_feature_flag():
    context = _workflow_context('{"subtasks":[{"task_id":"workspace_discovery","task_profile":"workspace_discovery","target":"."}]}')
    goal = GoalSpec(
        original_goal="demo",
        primary_intent="repository_overview",
        requires_repository_overview=True,
    )

    subtasks = decompose_goal(goal, context=context)

    assert subtasks == [
        SubTaskSpec(task_id="workspace_discovery", task_profile="workspace_discovery", target=".", metadata={"strategy": "model", "model_role": "planner"}),
    ]


def test_analyze_goal_treats_explicit_file_path_summary_as_file_read():
    goal = analyze_goal("请读取 src/service.py 并总结这个文件在做什么")

    assert goal.primary_intent == "file_read"
    assert goal.requires_file_read is True
    assert goal.target_paths == ["src/service.py"]


def test_analyze_goal_treats_modify_and_verify_request_as_change_intent():
    goal = analyze_goal("修改 README.md 并验证结果")

    assert goal.primary_intent == "change_and_verify"
    assert goal.requires_file_read is True
    assert goal.requires_final_synthesis is True
    assert goal.target_paths == ["README.md"]
    assert goal.metadata["requires_verification"] is True


def test_analyze_goal_extracts_filename_from_chinese_create_request_without_spaces():
    goal = analyze_goal("帮我在根目录下创建一个tet.txt文件，在里面加入鳄鱼的相关习性")

    assert goal.primary_intent == "change_and_verify"
    assert goal.target_paths == ["tet.txt"]
    assert goal.target_paths != ["帮我在根目录下创建一个tet.txt文件，在里面加入鳄鱼的相关习性"]


def test_analyze_goal_fallback_target_extraction_stays_conservative_for_chinese_sentence():
    goal = analyze_goal("请帮我处理一个复杂需求，不要把整句话当成路径，比如这里提到了tet.txt文件")

    assert goal.target_paths == ["tet.txt"]


def test_plan_next_subgraph_emits_apply_patch_for_targeted_replace_request():
    goal = GoalSpec(original_goal="把 README.md 中的 hello 替换成 hi，并验证结果", primary_intent="change_and_verify")
    envelope = SimpleNamespace(
        goal=goal.original_goal,
        normalized_goal=goal.original_goal,
        intent=goal.primary_intent,
        target_hints=["README.md"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-1", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=None)

    write_node = subgraph.nodes[0]
    assert write_node.node_type == "apply_patch"
    assert write_node.inputs["path"] == "README.md"


def test_plan_next_subgraph_emits_write_file_for_full_rewrite_request():
    goal = GoalSpec(original_goal="重写 README.md 为新的项目介绍，并验证结果", primary_intent="change_and_verify")
    envelope = SimpleNamespace(
        goal=goal.original_goal,
        normalized_goal=goal.original_goal,
        intent=goal.primary_intent,
        target_hints=["README.md"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-1b", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=None)

    write_node = subgraph.nodes[0]
    assert write_node.node_type == "write_file"
    assert write_node.inputs["path"] == "README.md"


def test_plan_next_subgraph_emits_append_text_for_append_request():
    goal = GoalSpec(original_goal="向 README.md 追加一行发布说明，并验证结果", primary_intent="change_and_verify")
    envelope = SimpleNamespace(
        goal=goal.original_goal,
        normalized_goal=goal.original_goal,
        intent=goal.primary_intent,
        target_hints=["README.md"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-1c", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=None)

    write_node = subgraph.nodes[0]
    assert write_node.node_type == "append_text"
    assert write_node.inputs["path"] == "README.md"


def test_plan_next_subgraph_prefers_clarification_for_underspecified_modify_request():
    goal = GoalSpec(original_goal="编辑 README.md 并提交", primary_intent="change_and_verify")
    envelope = SimpleNamespace(
        goal=goal.original_goal,
        normalized_goal=goal.original_goal,
        intent=goal.primary_intent,
        target_hints=["README.md"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-1d", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=None)

    clarification_node = subgraph.nodes[0]
    assert clarification_node.node_type == "clarification"
    assert "workspace_subtask" not in [node.node_type for node in subgraph.nodes]


def test_plan_next_subgraph_uses_clarification_for_unsupported_generic_request():
    goal = GoalSpec(original_goal="帮我整理这个仓库的后续开发事项", primary_intent="generic")
    envelope = SimpleNamespace(
        goal=goal.original_goal,
        normalized_goal=goal.original_goal,
        intent=goal.primary_intent,
        target_hints=[],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-1e", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=None)

    clarification_node = subgraph.nodes[0]
    assert clarification_node.node_type == "clarification"
    assert "workspace_subtask" not in [node.node_type for node in subgraph.nodes]


def test_plan_next_subgraph_keeps_native_file_read_without_compatibility_fallback():
    goal = GoalSpec(original_goal="读取 README.md", primary_intent="file_read")
    envelope = SimpleNamespace(
        goal=goal.original_goal,
        normalized_goal=goal.original_goal,
        intent=goal.primary_intent,
        target_hints=["README.md"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-2", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=None)

    assert subgraph.nodes[0].node_type == "content_search"
    assert "compatibility_mode" not in subgraph.nodes[0].inputs
    assert "fallback_reason" not in subgraph.nodes[0].inputs


def test_plan_next_subgraph_replans_to_verification_from_latest_judge_feedback():
    envelope = SimpleNamespace(
        goal="创建 tet.txt 并写入内容",
        normalized_goal="创建 tet.txt 并写入内容",
        intent="change_and_verify",
        target_hints=["tet.txt"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-feedback-1", goal_envelope=envelope)
    state.judge_history.append(
        JudgeDecision(
            status="needs_verification",
            reason="Verification coverage is missing",
            missing_evidence=["verification"],
            replan_hint={"next_node_type": "verification", "verification_type": "post_write"},
        )
    )

    subgraph = plan_next_subgraph(envelope, state, context=None)

    assert subgraph.nodes[0].node_type == "verification"
    assert subgraph.nodes[0].inputs["verification_type"] == "post_write"


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
    state.judge_history.append(
        JudgeDecision(
            status="needs_verification",
            reason="Verification coverage is missing",
            missing_evidence=["verification"],
            replan_hint={"next_node_type": "verification", "verification_type": "post_write"},
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

    subgraph = plan_next_subgraph(envelope, state, context=SimpleNamespace(application_context=context.application_context, services={"planner_mode": "deterministic"}))

    assert subgraph.metadata["strategy"] == "model"
    assert subgraph.nodes[0].node_type == "verification"


def test_plan_next_subgraph_emits_create_and_write_chain_for_create_with_content_request():
    envelope = SimpleNamespace(
        goal="在根目录下创建一个 tet.txt 文件，在里面加入鳄鱼的相关习性",
        normalized_goal="在根目录下创建一个 tet.txt 文件，在里面加入鳄鱼的相关习性",
        intent="change_and_verify",
        target_hints=["tet.txt"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-create-write", goal_envelope=envelope)

    subgraph = plan_next_subgraph(envelope, state, context=None)

    assert [node.node_type for node in subgraph.nodes[:2]] == ["create_path", "write_file"]
    assert subgraph.nodes[1].depends_on == [subgraph.nodes[0].node_id]
    assert subgraph.nodes[1].inputs["path"] == "tet.txt"


def test_plan_next_subgraph_replans_to_write_and_verification_when_judge_reports_missing_content():
    envelope = SimpleNamespace(
        goal="在根目录下创建一个 tet.txt 文件，在里面加入鳄鱼的相关习性",
        normalized_goal="在根目录下创建一个 tet.txt 文件，在里面加入鳄鱼的相关习性",
        intent="change_and_verify",
        target_hints=["tet.txt"],
        success_criteria=[],
        constraints={},
    )
    state = new_agent_graph_state(run_id="run-create-write-2", goal_envelope=envelope)
    state.judge_history.append(
        JudgeDecision(
            status="needs_more_evidence",
            reason="The file exists, but the requested content was not written or verified yet.",
            missing_evidence=["write_content", "verification"],
            replan_hint={
                "goal_gap": "content_missing",
                "recommended_next_actions": ["write_file", "verification"],
                "must_include": ["write_file", "verification"],
                "target_path": "tet.txt",
                "content_brief": "鳄鱼的相关习性",
            },
        )
    )

    subgraph = plan_next_subgraph(envelope, state, context=None)

    assert [node.node_type for node in subgraph.nodes[:2]] == ["write_file", "verification"]
