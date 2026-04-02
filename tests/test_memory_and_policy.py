from __future__ import annotations

from pathlib import Path

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.memory import (
    InMemoryIndexMemory,
    InMemorySessionMemory,
    MarkdownIndexMemory,
    MemoryRecord,
    WorkingMemory,
)
from agent_runtime_framework.policy import (
    PermissionLevel,
    PolicyDecision,
    SimpleDesktopPolicy,
)
from agent_runtime_framework.resources import LocalFileResourceRepository, ResourceRef
from agent_runtime_framework.workflow.context_assembly import build_runtime_context
from agent_runtime_framework.workflow.goal_intake import build_goal_envelope


def test_session_memory_tracks_recent_focus():
    memory = InMemorySessionMemory()
    focus = ResourceRef.for_path(Path("/tmp/demo.txt"))

    memory.remember_focus([focus], summary="read demo")

    snapshot = memory.snapshot()

    assert snapshot.last_summary == "read demo"
    assert snapshot.focused_resources == [focus]


def test_working_memory_is_run_scoped():
    memory = WorkingMemory()

    memory.set("resolved_resources", ["a"])

    assert memory.get("resolved_resources") == ["a"]
    memory.clear()
    assert memory.get("resolved_resources") is None


def test_index_memory_caches_values():
    memory = InMemoryIndexMemory()

    memory.put("summary:/tmp/a", {"text": "cached"})

    assert memory.get("summary:/tmp/a") == {"text": "cached"}


def test_index_memory_supports_relevance_search():
    memory = InMemoryIndexMemory()
    memory.remember(
        MemoryRecord(
            key="focus:src/service.py",
            text="service module handles billing workflows",
            kind="workspace_focus",
            metadata={"path": "src/service.py"},
        )
    )
    memory.remember(
        MemoryRecord(
            key="focus:docs/service.md",
            text="service documentation overview and usage notes",
            kind="workspace_focus",
            metadata={"path": "docs/service.md"},
        )
    )

    matches = memory.search("service billing module", limit=2)

    assert [match.metadata["path"] for match in matches] == [
        "src/service.py",
        "docs/service.md",
    ]


def test_markdown_index_memory_persists_records_and_searches_them(tmp_path: Path):
    memory_file = tmp_path / "agent-memory.md"
    memory = MarkdownIndexMemory(memory_file)
    memory.remember(
        MemoryRecord(
            key="focus:src/service.py",
            text="service module handles billing workflows",
            kind="workspace_focus",
            metadata={"path": "src/service.py", "summary": "service focus"},
        )
    )
    memory.remember(
        MemoryRecord(
            key="fact:docs/service.md",
            text="service documentation overview and usage notes",
            kind="workspace_fact",
            metadata={"path": "docs/service.md", "summary": "service docs"},
        )
    )

    assert memory_file.exists()
    persisted = memory_file.read_text(encoding="utf-8")
    assert "workspace_focus" in persisted
    assert "src/service.py" in persisted

    reloaded = MarkdownIndexMemory(memory_file)
    matches = reloaded.search("service billing module", limit=2)

    assert [match.metadata["path"] for match in matches] == [
        "src/service.py",
        "docs/service.md",
    ]


def test_markdown_index_memory_persists_values_across_reloads(tmp_path: Path):
    memory_file = tmp_path / "agent-memory.md"
    memory = MarkdownIndexMemory(memory_file)
    payload = {"goal": "请讲解 service 模块", "task_profile": "workspace_discovery"}

    memory.put("codex:pending_clarification", payload)

    reloaded = MarkdownIndexMemory(memory_file)

    assert reloaded.get("codex:pending_clarification") == payload


def test_index_memory_can_store_layered_memory_metadata():
    memory = InMemoryIndexMemory()
    record = MemoryRecord(
        key="memory:daily:listing",
        text="Found 18 entries.",
        kind="workspace_fact",
        metadata={
            "layer": "daily",
            "record_kind": "observation",
            "confidence": 0.2,
            "retrievable_for_resolution": False,
            "path": ".",
        },
    )

    memory.remember(record)
    matches = memory.search("entries", limit=1, kind="workspace_fact")

    assert matches
    assert matches[0].metadata["layer"] == "daily"
    assert matches[0].metadata["retrievable_for_resolution"] is False


def test_markdown_index_memory_persists_entity_binding_metadata(tmp_path: Path):
    memory_file = tmp_path / "agent-memory.md"
    memory = MarkdownIndexMemory(memory_file)
    memory.remember(
        MemoryRecord(
            key="entity:README",
            text="README maps to README.md",
            kind="entity_binding",
            metadata={
                "layer": "entity",
                "alias": "README",
                "path": "README.md",
                "entity_type": "file",
                "confidence": 0.98,
                "retrievable_for_resolution": True,
            },
        )
    )

    reloaded = MarkdownIndexMemory(memory_file)
    matches = reloaded.search("README", limit=1, kind="entity_binding")

    assert matches
    assert matches[0].metadata["path"] == "README.md"
    assert matches[0].metadata["retrievable_for_resolution"] is True


def test_application_context_uses_markdown_index_memory_for_workspace_defaults(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        config={"default_directory": str(workspace)},
    )

    assert isinstance(context.index_memory, MarkdownIndexMemory)
    assert context.index_memory.path == workspace / ".arf" / "memory.md"


def test_simple_desktop_policy_requires_confirmation_for_safe_write():
    policy = SimpleDesktopPolicy()

    decision = policy.authorize(PermissionLevel.SAFE_WRITE, confirmed=False)

    assert decision == PolicyDecision(
        allowed=True,
        requires_confirmation=True,
        reason="safe_write_requires_confirmation",
        safe_alternative="preview_only",
    )


def test_simple_desktop_policy_requires_confirmation_for_destructive_write():
    policy = SimpleDesktopPolicy()

    decision = policy.authorize(PermissionLevel.DESTRUCTIVE_WRITE, confirmed=False)

    assert decision.allowed is True
    assert decision.requires_confirmation is True
    assert decision.reason == "destructive_write_requires_confirmation"



def test_workspace_planner_uses_new_intent_terms(tmp_path: Path):
    from agent_runtime_framework.agents.workspace_backend.planner import infer_task_intent

    overview_intent = infer_task_intent("列一下当前工作区都有什么文件", workspace_root=tmp_path)
    read_intent = infer_task_intent("读取 README.md", workspace_root=tmp_path)
    compound_intent = infer_task_intent("总结 docs 目录并读取 README.md", workspace_root=tmp_path)

    assert overview_intent.task_kind == "workspace_discovery"
    assert read_intent.task_kind == "workspace_read"
    assert compound_intent.task_kind == "compound_read"


def test_goal_intake_builds_goal_envelope_with_memory_workspace_and_constraints(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        config={"default_directory": str(workspace), "max_dynamic_nodes": 3},
    )
    focus = ResourceRef.for_path(workspace / "README.md")
    app_context.session_memory.remember_focus([focus], summary="read README")

    goal = build_goal_envelope(
        "读取 README.md",
        application_context=app_context,
        workspace_root=workspace,
    )

    assert goal.goal == "读取 README.md"
    assert goal.intent == "file_read"
    assert goal.target_hints == ["README.md"]
    assert goal.memory_snapshot["last_summary"] == "read README"
    assert goal.workspace_snapshot["workspace_root"] == str(workspace)
    assert "README.md" in goal.workspace_snapshot["top_level_entries"]
    assert goal.constraints["max_dynamic_nodes"] == 3


def test_context_assembly_collects_application_workspace_memory_and_policy(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        config={"default_directory": str(workspace)},
    )
    focus = ResourceRef.for_path(workspace / "README.md")
    app_context.session_memory.remember_focus([focus], summary="focused")

    runtime_context = build_runtime_context(
        application_context=app_context,
        workspace_context={"workspace_root": str(workspace), "active_agent": "workspace"},
    )

    assert runtime_context["application_context"] is app_context
    assert runtime_context["workspace_context"]["workspace_root"] == str(workspace)
    assert runtime_context["memory"]["last_summary"] == "focused"
    assert runtime_context["session_memory_snapshot"].last_summary == "focused"
    assert runtime_context["policy_context"]["policy_name"] == "SimpleDesktopPolicy"
