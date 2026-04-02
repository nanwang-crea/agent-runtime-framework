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
from agent_runtime_framework.workflow.models import GoalSpec, SubTaskSpec


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
        SubTaskSpec(task_id="content_search", task_profile="content_search", target="README.md"),
        SubTaskSpec(task_id="chunked_file_read", task_profile="chunked_file_read", target="README.md", depends_on=["content_search"]),
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
        SubTaskSpec(task_id="workspace_discovery", task_profile="workspace_discovery", target="."),
        SubTaskSpec(task_id="content_search", task_profile="content_search", target="README.md"),
        SubTaskSpec(task_id="chunked_file_read", task_profile="chunked_file_read", target="README.md", depends_on=["content_search"]),
        SubTaskSpec(task_id="evidence_synthesis", task_profile="evidence_synthesis", depends_on=["workspace_discovery", "content_search", "chunked_file_read"]),
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
        SubTaskSpec(task_id="workspace_discovery", task_profile="workspace_discovery", target="."),
        SubTaskSpec(task_id="content_search", task_profile="content_search", target="README.md"),
        SubTaskSpec(task_id="chunked_file_read", task_profile="chunked_file_read", target="README.md", depends_on=["content_search"]),
        SubTaskSpec(task_id="evidence_synthesis", task_profile="evidence_synthesis", depends_on=["workspace_discovery", "content_search", "chunked_file_read"]),
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
        SubTaskSpec(task_id="workspace_discovery", task_profile="workspace_discovery", target="."),
    ]
