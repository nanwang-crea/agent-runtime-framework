from __future__ import annotations

from types import SimpleNamespace

from agent_runtime_framework.demo.agent_branch_orchestrator import AgentBranchOrchestrator
from agent_runtime_framework.workflow.models import GoalEnvelope, WorkflowGraph, WorkflowRun
from agent_runtime_framework.workflow.nodes.core import FinalResponseExecutor


class _FakeRuntime:
    def __init__(self, run: WorkflowRun) -> None:
        self.run_result = run
        self.calls: list[dict] = []

    def run(self, goal_envelope, **kwargs):
        self.calls.append({"goal_envelope": goal_envelope, **kwargs})
        self.run_result.metadata.setdefault("agent_graph_state", {})
        return self.run_result


def test_agent_branch_orchestrator_merges_clarification_into_prior_goal(monkeypatch):
    prior_goal = GoalEnvelope(
        goal="我需要你帮我看一下当前项目根目录当中的README在讲什么内容呢？",
        normalized_goal="我需要你帮我看一下当前项目根目录当中的README在讲什么内容呢？",
        intent="file_read",
        target_hints=[],
        success_criteria=["read target"],
    )
    prior_run = WorkflowRun(goal=prior_goal.goal, graph=WorkflowGraph())
    prior_run.metadata["goal_envelope"] = prior_goal.as_payload()
    prior_run.metadata["agent_graph_state"] = {
        "run_id": prior_run.run_id,
        "goal_envelope": prior_goal.as_payload(),
        "memory_state": {
            "clarification_memory": {"candidate_items": ["README.md", "frontend-shell/README.md"]},
            "semantic_memory": {},
            "execution_memory": {},
            "preference_memory": {},
        },
    }
    runtime = _FakeRuntime(WorkflowRun(goal=prior_goal.goal, graph=WorkflowGraph()))

    monkeypatch.setattr(
        "agent_runtime_framework.demo.agent_branch_orchestrator.resolve_clarification_response",
        lambda context, prior_goal_envelope, pending_request, user_response, prior_state: {
            "preferred_path": "README.md",
            "confirmed_target": "README.md",
            "excluded_targets": ["frontend-shell/README.md"],
            "updated_target_hints": ["README.md"],
            "should_reask": False,
            "confidence": 0.95,
            "reason": "user explicitly chose README.md",
        },
    )

    store = SimpleNamespace(load=lambda run_id: prior_run, save=lambda run: None)
    recorded = []
    orchestrator = AgentBranchOrchestrator(
        build_agent_graph_runtime=lambda: runtime,
        build_runtime_context=lambda: {},
        workflow_store=store,
        workflow_payload=lambda run: {"status": "completed"},
        remember_workflow_run=lambda message, run: None,
        application_context=None,
        workspace=".",
        context=None,
        get_pending_clarification=lambda: {"run_id": prior_run.run_id, "items": ["README.md", "frontend-shell/README.md"]},
        record_run=lambda payload, message: recorded.append((payload, message)),
        run_history_payload=lambda: [],
    )

    orchestrator.run("需要的是README.md这个文档", goal_spec=SimpleNamespace(original_goal="需要的是README.md这个文档", primary_intent="file_read", requires_target_interpretation=False, requires_search=False, requires_read=True, requires_verification=False, metadata={}), root_graph={"route": "agent", "intent": "file_read"})

    call = runtime.calls[0]
    assert call["goal_envelope"].goal == prior_goal.goal
    assert call["goal_envelope"].target_hints == ["README.md"]
    assert call["clarification_resolution"]["confirmed_target"] == "README.md"
    assert call["clarification_resolution"]["confirmed"] is True
    assert call["prior_state"]["memory_state"]["semantic_memory"]["confirmed_targets"] == ["README.md"]
    assert call["prior_state"]["memory_state"]["semantic_memory"]["interpreted_target"]["confirmed"] is True
    assert call["prior_state"]["memory_state"]["semantic_memory"]["interpreted_target"]["preferred_path"] == "README.md"


def test_final_response_executor_includes_response_memory_view(monkeypatch):
    captured = {}

    def _fake_synthesize(context, role, system_prompt, payload, max_tokens):
        captured["payload"] = payload
        return "final"

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.core.synthesize_text",
        _fake_synthesize,
    )

    run = WorkflowRun(goal="解释根目录 README")
    run.shared_state["judge_decision"] = {"status": "accepted", "reason": "done"}
    run.shared_state["aggregated_result"] = SimpleNamespace(
        output={"summaries": ["readme summary"], "facts": [], "evidence_items": [], "verification": None},
        references=["README.md"],
    )
    run.shared_state["memory_state"] = {
        "clarification_memory": {"last_resolution": {"preferred_path": "README.md"}},
        "semantic_memory": {"confirmed_targets": ["README.md"], "excluded_targets": ["frontend-shell/README.md"]},
        "execution_memory": {},
        "preference_memory": {},
    }

    result = FinalResponseExecutor().execute(SimpleNamespace(node_id="final", node_type="final_response", metadata={}), run, context={})

    assert result.output["final_response"] == "final"
    assert captured["payload"]["response_memory_view"]["confirmed_targets"] == ["README.md"]
    assert captured["payload"]["response_memory_view"]["excluded_targets"] == ["frontend-shell/README.md"]
