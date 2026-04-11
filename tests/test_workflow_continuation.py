from __future__ import annotations

from types import SimpleNamespace

from agent_runtime_framework.api.services.chat_service import ChatService
from agent_runtime_framework.api.services.run_service import RunService
from agent_runtime_framework.memory import MemoryManager
from agent_runtime_framework.workflow.interaction.clarification_resolution import resolve_clarification_response
from agent_runtime_framework.workflow.state.models import GoalEnvelope, InteractionRequest, RUN_STATUS_WAITING_INPUT, WorkflowGraph, WorkflowRun, new_agent_graph_state
from agent_runtime_framework.workflow.nodes.core import FinalResponseExecutor


class _FakeRuntime:
    def __init__(self, run: WorkflowRun) -> None:
        self.run_result = run
        self.calls: list[dict] = []
        self.context: dict = {}

    def run(self, goal_envelope, **kwargs):
        self.calls.append({"goal_envelope": goal_envelope, **kwargs})
        self.run_result.metadata.setdefault("agent_graph_state", {})
        return self.run_result


def test_chat_service_merges_clarification_into_prior_goal(monkeypatch):
    prior_goal = GoalEnvelope(
        goal="我需要你帮我看一下当前项目根目录当中的README在讲什么内容呢？",
        normalized_goal="我需要你帮我看一下当前项目根目录当中的README在讲什么内容呢？",
        intent="file_read",
        target_hints=[],
        success_criteria=["read target"],
    )
    prior_run = WorkflowRun(goal=prior_goal.goal, graph=WorkflowGraph())
    prior_run.metadata["goal_envelope"] = prior_goal.as_payload()
    prior_run.pending_interaction = InteractionRequest(
        kind="clarification",
        prompt="请确认 README 路径",
        items=["README.md", "frontend-shell/README.md"],
    )
    prior_run.metadata["agent_graph_state"] = {
        "run_id": prior_run.run_id,
        "goal_envelope": prior_goal.as_payload(),
        "memory_state": {
            "session_memory": {
                "last_active_target": None,
                "recent_paths": [],
                "last_action_summary": None,
                "last_read_files": [],
                "last_clarification": {"candidate_items": ["README.md", "frontend-shell/README.md"]},
            },
            "working_memory": {
                "active_target": None,
                "confirmed_targets": [],
                "excluded_targets": [],
                "current_step": None,
                "open_issues": [],
                "last_tool_result_summary": None,
            },
            "long_term_memory": {},
        },
    }
    runtime = _FakeRuntime(WorkflowRun(goal=prior_goal.goal, graph=WorkflowGraph()))

    monkeypatch.setattr(
        "agent_runtime_framework.api.services.chat_service.resolve_clarification_response",
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
    manager_calls = []
    real_manager = MemoryManager()

    store = SimpleNamespace(load=lambda run_id: prior_run, save=lambda run: None)
    recorded = []
    session_responses = SimpleNamespace(
        memory_payload=lambda: {},
        run_history_payload=lambda: [],
        session_payload=lambda: {},
        plan_history_payload=lambda: [],
        context_payload=lambda: {},
    )
    error_responses = SimpleNamespace(error_payload=lambda exc: {"status": "error", "error": str(exc)})
    chat_service = ChatService(
        SimpleNamespace(
            workflow_runtime_context=lambda: {},
            _workflow_store=store,
            _pending_workflow_interaction={"run_id": prior_run.run_id, "kind": "clarification", "items": ["README.md", "frontend-shell/README.md"]},
                context=SimpleNamespace(
                    application_context=SimpleNamespace(
                        memory_manager=SimpleNamespace(
                            update_session_memory=lambda memory_state, **kwargs: (
                                manager_calls.append(("session", kwargs)),
                                real_manager.update_session_memory(memory_state, **kwargs),
                            )[-1],
                            update_working_memory=lambda memory_state, **kwargs: (
                                manager_calls.append(("working", kwargs)),
                                real_manager.update_working_memory(memory_state, **kwargs),
                            )[-1],
                        )
                    )
                ),
            workspace=".",
            record_run=lambda payload, message: recorded.append((payload, message)),
        )
        ,
        session_responses,
        error_responses,
    )
    monkeypatch.setattr(ChatService, "_agent_runtime", lambda self, process_sink=None: runtime)
    monkeypatch.setattr(ChatService, "_workflow_payload", lambda self, run, resume_token_id=None: {"status": "completed"})
    monkeypatch.setattr(ChatService, "_remember_workflow_run", lambda self, message, run: None)

    chat_service._run_agent_branch(
        "需要的是README.md这个文档",
        goal_spec=SimpleNamespace(
            original_goal="需要的是README.md这个文档",
            primary_intent="file_read",
            requires_target_interpretation=False,
            requires_search=False,
            requires_read=True,
            requires_verification=False,
            metadata={},
        ),
        root_graph={"route": "agent", "intent": "file_read"},
    )

    call = runtime.calls[0]
    assert call["goal_envelope"].goal == prior_goal.goal
    assert call["goal_envelope"].target_hints == ["README.md"]
    assert call["clarification_resolution"]["confirmed_target"] == "README.md"
    assert call["clarification_resolution"]["confirmed"] is True
    assert manager_calls
    assert call["prior_state"]["memory_state"]["working_memory"]["confirmed_targets"] == ["README.md"]
    assert call["prior_state"]["memory_state"]["working_memory"]["active_target"] == "README.md"
    assert call["prior_state"]["memory_state"]["session_memory"]["last_clarification"]["confirmed_target"] == "README.md"


def test_chat_service_workflow_payload_exposes_pending_interaction():
    run = WorkflowRun(goal="look at readme")
    run.status = RUN_STATUS_WAITING_INPUT
    run.pending_interaction = InteractionRequest(
        kind="clarification",
        prompt="Which README should I inspect?",
        items=["README.md", "frontend-shell/README.md"],
    )

    runtime_state = SimpleNamespace(
        workspace=".",
        _last_route_decision=None,
        _pending_workflow_interaction=None,
    )
    session_responses = SimpleNamespace(
        session_payload=lambda: {},
        plan_history_payload=lambda: [],
        run_history_payload=lambda: [],
        memory_payload=lambda: {},
        context_payload=lambda: {},
    )
    chat_service = ChatService(runtime_state, session_responses, SimpleNamespace())

    payload = chat_service._workflow_payload(run)

    assert payload["status"] == RUN_STATUS_WAITING_INPUT
    assert payload["final_answer"] == "Which README should I inspect?"
    assert payload["pending_interaction"]["kind"] == "clarification"
    assert runtime_state._pending_workflow_interaction["run_id"] == run.run_id


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
    state = new_agent_graph_state(
        run_id="final-response-memory",
        goal_envelope=GoalEnvelope(goal="解释根目录 README", normalized_goal="解释根目录 README", intent="file_read"),
    )
    state.memory_state.session_memory.last_active_target = "README.md"
    state.memory_state.session_memory.recent_paths = ["README.md"]
    state.memory_state.session_memory.last_action_summary = "read readme"
    state.memory_state.session_memory.last_read_files = ["README.md"]
    state.memory_state.session_memory.last_clarification = {"preferred_path": "README.md"}
    state.memory_state.working_memory.active_target = "README.md"
    state.memory_state.working_memory.confirmed_targets = ["README.md"]
    state.memory_state.working_memory.excluded_targets = ["frontend-shell/README.md"]
    state.memory_state.working_memory.current_step = "explain readme"

    result = FinalResponseExecutor().execute(
        SimpleNamespace(node_id="final", node_type="final_response", metadata={}),
        run,
        context=SimpleNamespace(agent_graph_state=state),
    )

    assert result.output["final_response"] == "final"
    assert captured["payload"]["response_memory_view"]["confirmed_targets"] == ["README.md"]
    assert captured["payload"]["response_memory_view"]["excluded_targets"] == ["frontend-shell/README.md"]


def test_run_service_approve_resumes_pending_token_from_runtime_state():
    resumed = WorkflowRun(goal="demo", graph=WorkflowGraph())
    resumed.final_output = "done"

    class _Runtime:
        def resume(self, run, *, resume_token, approved):
            assert run is resumed
            assert resume_token == "resume-token"
            assert approved is True
            return resumed

    runtime_state = SimpleNamespace(
        _pending_tokens={"token-1": {"runtime": _Runtime(), "run": resumed, "token": "resume-token"}},
        workspace=".",
        record_run=lambda payload, prompt: None,
        context=SimpleNamespace(session=None),
        _task_history=[],
        _run_inputs={},
        _workflow_store=SimpleNamespace(load=lambda run_id: resumed),
        _pending_workflow_interaction=None,
        _last_route_decision=None,
    )
    session_responses = SimpleNamespace(
        session_payload=lambda: {"session_id": "s", "turns": []},
        plan_history_payload=lambda: [],
        run_history_payload=lambda: [],
        memory_payload=lambda: {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None},
    )
    chat_service = SimpleNamespace(
        _workflow_payload=lambda run, resume_token_id=None: {"status": run.status},
        _register_pending_run=lambda run: None,
        _remember_workflow_run=lambda message, run: None,
    )

    payload = RunService(runtime_state, session_responses, chat_service).approve("token-1", True)

    assert payload["status"] == resumed.status
    assert "token-1" not in runtime_state._pending_tokens


def test_run_service_replay_falls_back_to_chat_when_run_is_missing(monkeypatch):
    runtime_state = SimpleNamespace(
        _pending_tokens={},
        _run_inputs={"run-1": "hello"},
        _workflow_store=SimpleNamespace(load=lambda run_id: (_ for _ in ()).throw(FileNotFoundError(run_id))),
        workspace=".",
        record_run=lambda payload, prompt: None,
    )
    session_responses = SimpleNamespace(
        session_payload=lambda: {"session_id": "s", "turns": []},
        plan_history_payload=lambda: [],
        run_history_payload=lambda: [],
        memory_payload=lambda: {"focused_resource": None, "recent_resources": [], "last_summary": None, "active_capability": None},
    )
    chat_service = SimpleNamespace(
        _workflow_payload=lambda run, resume_token_id=None: {"status": run.status},
        _register_pending_run=lambda run: None,
        _remember_workflow_run=lambda message, run: None,
        chat=lambda message: {"status": "completed", "run_id": "run-2", "final_answer": message},
    )

    payload = RunService(runtime_state, session_responses, chat_service).replay("run-1")

    assert payload["status"] == "completed"
    assert payload["final_answer"] == "hello"


def test_resolve_clarification_response_repairs_semantically_invalid_payload(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.interaction.clarification_resolution.chat_json",
        lambda *args, **kwargs: (
            captured.update({"payload": kwargs.get("payload")})
            or {
                "preferred_path": "",
                "confirmed_target": "",
                "updated_target_hints": [],
                "should_reask": False,
                "confidence": 0.8,
                "reason": "still ambiguous",
            }
        ),
    )
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.interaction.clarification_resolution.repair_structured_contract",
        lambda *args, **kwargs: {
            "preferred_path": "README.md",
            "confirmed_target": "README.md",
            "updated_target_hints": ["README.md"],
            "should_reask": False,
            "confidence": 0.92,
            "reason": "user picked README.md",
        },
    )

    resolved = resolve_clarification_response(
        context=SimpleNamespace(),
        prior_goal_envelope={"goal": "读 README", "target_hints": ["README.md", "frontend-shell/README.md"]},
        pending_request={"kind": "clarification"},
        user_response="就看 README.md",
        prior_state={"memory_state": {}},
    )

    assert resolved["confirmed_target"] == "README.md"
    assert resolved["confirmed"] is True
    assert "memory_state" not in captured["payload"]
    assert "task_snapshot" in captured["payload"]
    assert "working_memory_view" in captured["payload"]
