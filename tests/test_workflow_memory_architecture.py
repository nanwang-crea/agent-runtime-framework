from __future__ import annotations

from agent_runtime_framework.memory import MemoryManager, TaskSnapshot
from agent_runtime_framework.workflow.state.graph_state_store import AgentGraphStateStore
from agent_runtime_framework.workflow.state.models import (
    ConversationTurn,
    GoalEnvelope,
    SessionMemoryState,
    WorkflowMemoryState,
    WorkingMemory,
)


def test_memory_manager_builds_trimmed_task_snapshot_from_session_and_transcript():
    manager = MemoryManager()
    session_memory = SessionMemoryState(
        last_active_target="docs/guide.md",
        recent_paths=["README.md", "docs/guide.md", "README.md", "src/app.py"],
        last_action_summary="Read the guide and noted follow-up work.",
        last_read_files=["README.md", "docs/guide.md"],
        last_clarification={"question": "Which guide?", "answer": "docs/guide.md"},
    )
    long_term_memory = {
        "user_preferences": {"language": "zh-CN"},
        "project_conventions": {"docs_root": "docs"},
        "path_aliases": {"guide": "docs/guide.md"},
    }
    transcript = [
        ConversationTurn(role="user", content="先看看 README"),
        ConversationTurn(role="assistant", content="我已经读完 README 并总结了重点。"),
        ConversationTurn(role="user", content="再解释一下刚才读的 guide 文档。"),
    ]

    snapshot = manager.build_task_snapshot(
        session_memory=session_memory,
        long_term_memory=long_term_memory,
        transcript=transcript,
    )

    assert isinstance(snapshot, TaskSnapshot)
    assert snapshot.goal == "再解释一下刚才读的 guide 文档。"
    assert snapshot.recent_focus == ["docs/guide.md", "README.md"]
    assert snapshot.recent_paths == ["README.md", "docs/guide.md", "src/app.py"]
    assert snapshot.last_action_summary == "Read the guide and noted follow-up work."
    assert snapshot.last_clarification == {"question": "Which guide?", "answer": "docs/guide.md"}
    assert snapshot.long_term_hints == long_term_memory


def test_memory_manager_initializes_and_restores_minimal_working_memory():
    manager = MemoryManager()
    snapshot = TaskSnapshot(
        goal="Delete the file just created.",
        recent_focus=["testes.txt"],
        recent_paths=["testes.txt", "notes/today.md"],
        last_action_summary="Created testes.txt in the workspace root.",
        last_clarification=None,
        long_term_hints={},
    )

    working_memory = manager.init_working_memory(snapshot)

    assert working_memory.active_target == "testes.txt"
    assert working_memory.confirmed_targets == ["testes.txt"]
    assert working_memory.excluded_targets == []
    assert working_memory.current_step == "Delete the file just created."
    assert working_memory.open_issues == []
    assert working_memory.last_tool_result_summary is None

    checkpoint = manager.checkpoint_working_memory(working_memory)

    assert checkpoint == {
        "active_target": "testes.txt",
        "confirmed_targets": ["testes.txt"],
        "excluded_targets": [],
        "current_step": "Delete the file just created.",
        "open_issues": [],
        "last_tool_result_summary": None,
    }

    restored = manager.restore_working_memory(checkpoint)

    assert restored == working_memory


def test_graph_state_store_restores_working_memory_only():
    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="file_read")

    state = AgentGraphStateStore().restore_state(
        goal,
        run_id="run-memory",
        prior_state={
            "run_id": "run-memory",
            "goal_envelope": goal.as_payload(),
            "memory_state": {
                "working_memory": {
                    "active_target": "README.md",
                    "confirmed_targets": ["README.md"],
                    "excluded_targets": ["docs/README.md"],
                    "current_step": "Explain the active README",
                    "open_issues": ["need summary"],
                    "last_tool_result_summary": {"tool": "read_file", "path": "README.md"},
                }
            },
        },
    )

    assert state.memory_state.working_memory == WorkingMemory(
        active_target="README.md",
        confirmed_targets=["README.md"],
        excluded_targets=["docs/README.md"],
        current_step="Explain the active README",
        open_issues=["need summary"],
        last_tool_result_summary={"tool": "read_file", "path": "README.md"},
    )


def test_memory_models_restore_from_payload():
    session_memory = SessionMemoryState.from_payload(
        {
            "last_active_target": "README.md",
            "recent_paths": ["README.md", "docs/README.md"],
            "last_action_summary": "read readme",
            "last_read_files": ["README.md"],
            "last_clarification": {"preferred_path": "README.md"},
        }
    )
    working_memory = WorkingMemory.from_payload(
        {
            "active_target": "README.md",
            "confirmed_targets": ["README.md"],
            "excluded_targets": ["docs/README.md"],
            "current_step": "explain readme",
            "open_issues": ["need summary"],
            "last_tool_result_summary": {"tool_name": "read_file"},
        }
    )
    workflow_memory = WorkflowMemoryState.from_payload(
        {
            "session_memory": session_memory.as_payload(),
            "working_memory": working_memory.as_payload(),
            "long_term_memory": {"path_aliases": {"readme": "README.md"}},
        }
    )

    assert workflow_memory.session_memory.last_active_target == "README.md"
    assert workflow_memory.working_memory.confirmed_targets == ["README.md"]
    assert workflow_memory.long_term_memory["path_aliases"]["readme"] == "README.md"


def test_memory_manager_invalidates_stale_working_memory_against_session_memory():
    manager = MemoryManager()
    working_memory = WorkingMemory(
        active_target="stale.txt",
        confirmed_targets=["stale.txt"],
        excluded_targets=["other.txt"],
        current_step="Delete stale file",
        open_issues=["confirm target"],
        last_tool_result_summary={"tool": "create_path"},
    )
    session_memory = SessionMemoryState(
        last_active_target="fresh.txt",
        recent_paths=["fresh.txt"],
        last_action_summary="Created fresh.txt.",
        last_read_files=["fresh.txt"],
        last_clarification=None,
    )

    validated = manager.validate_working_memory(
        working_memory,
        session_memory=session_memory,
    )

    assert validated.active_target is None
    assert validated.confirmed_targets == []
    assert validated.excluded_targets == ["other.txt"]
    assert validated.current_step is None


def test_memory_manager_updates_workflow_memory_state_in_place():
    manager = MemoryManager()
    memory_state = WorkflowMemoryState()

    manager.update_session_memory(
        memory_state,
        last_active_target="README.md",
        recent_paths=["README.md", "docs/README.md"],
        last_action_summary="read readme",
        last_clarification={"preferred_path": "README.md"},
    )
    manager.update_working_memory(
        memory_state,
        active_target="README.md",
        confirmed_targets=["README.md"],
        excluded_targets=["docs/README.md"],
        current_step="explain readme",
    )

    assert memory_state.session_memory.last_active_target == "README.md"
    assert memory_state.session_memory.last_clarification == {"preferred_path": "README.md"}
    assert memory_state.working_memory.confirmed_targets == ["README.md"]
    assert memory_state.working_memory.excluded_targets == ["docs/README.md"]
