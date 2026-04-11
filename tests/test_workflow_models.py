from agent_runtime_framework.workflow import (
    InteractionRequest,
    NODE_STATUS_COMPLETED,
    NODE_STATUS_PENDING,
    RUN_STATUS_PENDING,
    RUN_STATUS_WAITING_INPUT,
    AgentGraphState,
    GoalEnvelope,
    JudgeDecision,
    NodeResult,
    NodeState,
    PlannedNode,
    PlannedSubgraph,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)
from pathlib import Path
from types import SimpleNamespace


def test_workflow_run_tracks_graph_and_node_states():
    analyze = WorkflowNode(node_id="analyze", node_type="analysis")
    finish = WorkflowNode(node_id="finish", node_type="final_response")
    graph = WorkflowGraph(
        nodes=[analyze, finish],
        edges=[WorkflowEdge(source="analyze", target="finish")],
    )
    run = WorkflowRun(
        goal="read README and summarize",
        graph=graph,
        node_states={"analyze": NodeState(node_id="analyze")},
    )

    assert run.goal == "read README and summarize"
    assert run.status == RUN_STATUS_PENDING
    assert run.graph is graph
    assert run.shared_state == {}
    assert run.pending_interaction is None
    assert run.node_states["analyze"].status == NODE_STATUS_PENDING


def test_workflow_node_supports_dependency_metadata_and_execution_policy_fields():
    node = WorkflowNode(
        node_id="summarize",
        node_type="final_response",
        dependencies=["analyze", "readme"],
        task_profile="final_synthesis",
        requires_approval=True,
        retry_limit=2,
        metadata={"audience": "user"},
    )

    assert node.dependencies == ["analyze", "readme"]
    assert node.task_profile == "final_synthesis"
    assert node.requires_approval is True
    assert node.retry_limit == 2
    assert node.metadata == {"audience": "user"}


def test_node_state_and_result_capture_result_error_and_approval_data():
    result = NodeResult(
        status=NODE_STATUS_COMPLETED,
        output={"summary": "done"},
        references=["README.md"],
        interaction_request=InteractionRequest(kind="clarification", prompt="Which README?", items=["README.md"]),
    )
    state = NodeState(
        node_id="summarize",
        status=NODE_STATUS_COMPLETED,
        result=result,
        error="",
        approval_requested=True,
        approval_granted=False,
    )

    assert state.result is result
    assert state.result.output == {"summary": "done"}
    assert state.result.references == ["README.md"]
    assert state.result.interaction_request.kind == "clarification"
    assert state.result.interaction_request.items == ["README.md"]
    assert state.approval_requested is True
    assert state.approval_granted is False
    assert state.error == ""


def test_workflow_models_expose_stable_status_values():
    node = WorkflowNode(node_id="plan", node_type="planner")
    run = WorkflowRun(goal="plan task")
    result = NodeResult(status=NODE_STATUS_COMPLETED)

    assert RUN_STATUS_PENDING == "pending"
    assert RUN_STATUS_WAITING_INPUT == "waiting_input"
    assert NODE_STATUS_PENDING == "pending"
    assert NODE_STATUS_COMPLETED == "completed"
    assert node.status == NODE_STATUS_PENDING
    assert run.status == RUN_STATUS_PENDING
    assert result.status == NODE_STATUS_COMPLETED



def test_workflow_payload_helpers_normalize_aggregated_schema():
    from agent_runtime_framework.workflow.state.models import normalize_aggregated_workflow_payload

    payload = normalize_aggregated_workflow_payload(
        {
            "summary": "workspace summary",
            "facts": [{"kind": "entrypoint", "path": "README.md"}],
            "evidence_items": [{"kind": "path", "path": "README.md", "summary": "README"}],
            "verification": {"status": "passed", "success": True, "summary": "verified"},
        }
    )

    assert payload["summaries"] == ["workspace summary"]
    assert payload["facts"] == [{"kind": "entrypoint", "path": "README.md"}]
    assert payload["evidence_items"] == [{"kind": "path", "path": "README.md", "summary": "README"}]
    assert payload["verification"] == {"status": "passed", "success": True, "summary": "verified"}
    assert payload["verification_events"] == [{"status": "passed", "success": True, "summary": "verified"}]
    assert payload["chunks"] == []
    assert payload["artifacts"] == {}
    assert payload["open_questions"] == []


def test_agent_graph_models_support_defaults_and_serialization_helpers():
    planned_node = PlannedNode(
        node_id="search_docs",
        node_type="content_search",
        reason="Need primary evidence",
        inputs={"query": "agent graph runtime"},
        success_criteria=["find matching files"],
    )
    subgraph = PlannedSubgraph(
        iteration=1,
        planner_summary="Search relevant docs first",
        nodes=[planned_node],
        edges=[WorkflowEdge(source="plan_1", target="search_docs")],
    )
    judge = JudgeDecision(status="needs_more_evidence", reason="Need more sources")
    goal = GoalEnvelope(
        goal="总结 agent graph runtime 设计",
        normalized_goal="总结 agent graph runtime 设计",
        intent="summarize",
        target_hints=["docs/plans/2026-04-01-agent-graph-runtime-design.md"],
        success_criteria=["provide a clear summary"],
    )

    assert planned_node.depends_on == []
    assert goal.memory_snapshot == {}
    assert goal.workspace_snapshot == {}
    assert goal.policy_context == {}
    assert goal.constraints == {}
    assert judge.missing_evidence == []
    assert judge.coverage_report == {}
    assert judge.replan_hint == {}

    from agent_runtime_framework.workflow.state.models import (
        new_agent_graph_state,
        serialize_agent_graph_state,
    )

    state = new_agent_graph_state(run_id="run-1", goal_envelope=goal)

    assert state.run_id == "run-1"
    assert state.current_iteration == 0
    assert state.aggregated_payload["summaries"] == []
    assert state.planned_subgraphs == []
    assert state.judge_history == []
    assert state.appended_node_ids == []
    assert state.iteration_summaries == []
    assert state.failure_history == []
    assert state.open_issues == []
    assert state.attempted_strategies == []
    assert state.recovery_history == []
    assert state.repair_history == []
    assert state.memory_state.session_memory.last_active_target is None
    assert state.memory_state.session_memory.recent_paths == []
    assert state.memory_state.working_memory.active_target is None
    assert state.memory_state.working_memory.confirmed_targets == []
    assert state.memory_state.long_term_memory == {}

    payload = serialize_agent_graph_state(state)

    assert payload["run_id"] == "run-1"
    assert payload["goal_envelope"]["goal"] == "总结 agent graph runtime 设计"
    assert payload["aggregated_payload"]["summaries"] == []
    assert payload["execution_summary"]["current_iteration"] == 0
    assert payload["execution_summary"]["last_judge_status"] == ""
    assert payload["execution_summary"]["attempted_strategies"] == []
    assert payload["iteration_summaries"] == []
    assert payload["failure_history"] == []
    assert payload["open_issues"] == []
    assert payload["attempted_strategies"] == []
    assert payload["recovery_history"] == []
    assert payload["repair_history"] == []
    assert payload["memory_state"] == {
        "session_memory": {
            "last_active_target": None,
            "recent_paths": [],
            "last_action_summary": None,
            "last_read_files": [],
            "last_clarification": None,
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
    }

    serialized_subgraph = subgraph.as_payload()
    serialized_judge = judge.as_payload()

    assert serialized_subgraph["nodes"][0]["reason"] == "Need primary evidence"
    assert serialized_judge["status"] == "needs_more_evidence"
    assert serialized_judge["replan_hint"] == {}
    assert serialized_judge["diagnosis"] == {}
    assert serialized_judge["strategy_guidance"] == {}


def test_judge_decision_serializes_route_constraints():
    decision = JudgeDecision(
        status="replan",
        reason="Need grounded README content",
        missing_evidence=["read README body"],
        coverage_report={"evidence_gap": "missing_direct_read"},
        replan_hint={"preferred_strategy": "direct_read_confirmed_target"},
        diagnosis={"primary_gap": "missing_read_grounding"},
        strategy_guidance={"recommended_strategy": "read_before_answering"},
        recommended_recovery_mode="collect_more_evidence",
        verification_required=False,
        human_handoff_required=False,
        allowed_next_node_types=["plan_read", "chunked_file_read"],
        blocked_next_node_types=["final_response"],
        must_cover=["read README body"],
        planner_instructions="Read the README content before answering.",
    )

    payload = decision.as_payload()

    assert payload["status"] == "replan"
    assert payload["allowed_next_node_types"] == ["plan_read", "chunked_file_read"]
    assert payload["blocked_next_node_types"] == ["final_response"]
    assert payload["must_cover"] == ["read README body"]
    assert payload["planner_instructions"] == "Read the README content before answering."
    assert payload["recommended_recovery_mode"] == "collect_more_evidence"


def test_agent_graph_state_store_restores_repair_history():
    from agent_runtime_framework.workflow.state.graph_state_store import AgentGraphStateStore

    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="file_read")
    state = AgentGraphStateStore().restore_state(
        goal,
        run_id="repair-state-1",
        prior_state={
            "run_id": "repair-state-1",
            "goal_envelope": goal.as_payload(),
            "repair_history": [
                {
                    "contract_kind": "read_plan",
                    "role": "planner",
                    "success": True,
                    "attempts_used": 2,
                    "max_attempts": 3,
                    "initial_error": "missing preferred_regions",
                }
            ],
            "memory_state": {},
        },
    )

    assert state.repair_history[0]["contract_kind"] == "read_plan"
    assert state.repair_history[0]["attempts_used"] == 2


def test_workflow_prompt_helpers_are_owned_by_workflow_layer():
    root = Path(__file__).resolve().parents[1]
    workflow_files = [
        root / "agent_runtime_framework" / "workflow" / "planning" / "goal_analysis.py",
        root / "agent_runtime_framework" / "workflow" / "planning" / "decomposition.py",
        root / "agent_runtime_framework" / "workflow" / "planning" / "subgraph_planner.py",
        root / "agent_runtime_framework" / "workflow" / "llm" / "access.py",
        root / "agent_runtime_framework" / "workflow" / "interaction" / "conversation_messages.py",
    ]

    for path in workflow_files:
        source = path.read_text(encoding="utf-8")
        assert "agents.workspace_backend.prompting" not in source
        assert "agents.workspace_backend.run_context" not in source


def test_subgraph_planner_prompt_mentions_strategy_change_and_failure_history():
    from agent_runtime_framework.workflow.planning.prompts import build_subgraph_planner_system_prompt

    prompt = build_subgraph_planner_system_prompt()

    assert "task_snapshot" in prompt
    assert "working_memory_view" in prompt
    assert "change strategy" in prompt.lower()


def test_agent_graph_state_store_restores_workflow_memory_state():
    from agent_runtime_framework.workflow.state.graph_state_store import AgentGraphStateStore

    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="file_read")
    state = AgentGraphStateStore().restore_state(
        goal,
        run_id="run-memory",
        prior_state={
            "run_id": "run-memory",
            "goal_envelope": goal.as_payload(),
            "memory_state": {
                "session_memory": {
                    "last_active_target": "README.md",
                    "recent_paths": ["README.md"],
                    "last_action_summary": "read readme",
                    "last_read_files": ["README.md"],
                    "last_clarification": {"preferred_path": "README.md"},
                },
                "working_memory": {
                    "active_target": "README.md",
                    "confirmed_targets": ["README.md"],
                    "excluded_targets": ["docs/README.md"],
                    "current_step": "explain readme",
                    "open_issues": ["need summary"],
                    "last_tool_result_summary": {"tool_name": "read_file"},
                },
                "long_term_memory": {"path_aliases": {"readme": "README.md"}},
            },
        },
    )

    assert state.memory_state.session_memory.last_active_target == "README.md"
    assert state.memory_state.session_memory.last_clarification == {"preferred_path": "README.md"}
    assert state.memory_state.working_memory.confirmed_targets == ["README.md"]
    assert state.memory_state.working_memory.excluded_targets == ["docs/README.md"]
    assert state.memory_state.long_term_memory["path_aliases"]["readme"] == "README.md"


def test_model_context_compact_structured_workflow_memory():
    from agent_runtime_framework.workflow.context.model_context import WorkflowModelContextBuilder
    from agent_runtime_framework.workflow.state.models import new_agent_graph_state

    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="file_read")
    state = new_agent_graph_state(run_id="run-memory-view", goal_envelope=goal)
    state.open_issues = ["verification"]
    state.failure_history = [
        {"iteration": 1, "status": "needs_more_evidence", "reason": "a"},
        {"iteration": 2, "status": "needs_verification", "reason": "b"},
        {"iteration": 3, "status": "needs_clarification", "reason": "c"},
    ]
    state.recovery_history = [
        {"trigger": "needs_more_evidence", "action": "replan"},
        {"trigger": "needs_verification", "action": "replan"},
        {"trigger": "needs_clarification", "action": "request_clarification"},
    ]
    state.memory_state.session_memory.last_active_target = "README.md"
    state.memory_state.session_memory.recent_paths = ["README.md", "frontend-shell/README.md"]
    state.memory_state.session_memory.last_read_files = ["README.md"]
    state.memory_state.session_memory.last_clarification = {"candidate_items": ["README.md", "frontend-shell/README.md"]}
    state.memory_state.working_memory.confirmed_targets = ["README.md"]
    state.memory_state.working_memory.excluded_targets = ["frontend-shell/README.md"]
    state.memory_state.working_memory.open_issues = ["verification"]
    state.memory_state.working_memory.last_tool_result_summary = {"summary": "inspect docs"}

    builder = WorkflowModelContextBuilder()
    task_snapshot = builder.build_task_snapshot_fragment(state)
    working_view = builder.build_working_memory_fragment(state)

    assert task_snapshot["recent_focus"] == ["README.md"]
    assert task_snapshot["recent_paths"] == ["README.md", "frontend-shell/README.md"]
    assert task_snapshot["last_clarification"]["candidate_items"] == ["README.md", "frontend-shell/README.md"]
    assert working_view["confirmed_targets"] == ["README.md"]
    assert working_view["excluded_targets"] == ["frontend-shell/README.md"]
    assert working_view["open_issues"] == ["verification"]


def test_workflow_model_context_builder_restores_prior_state_for_clarification_and_response():
    from agent_runtime_framework.workflow.context.model_context import WorkflowModelContextBuilder

    builder = WorkflowModelContextBuilder()
    prior_state = {
        "memory_state": {
            "session_memory": {
                "last_active_target": "README.md",
                "recent_paths": ["README.md", "docs/README.md"],
                "last_action_summary": "read root readme",
                "last_read_files": ["README.md"],
                "last_clarification": {"candidate_items": ["README.md", "docs/README.md"]},
            },
            "working_memory": {
                "active_target": "README.md",
                "confirmed_targets": ["README.md"],
                "excluded_targets": ["docs/README.md"],
                "current_step": "read readme",
                "open_issues": ["verification"],
                "last_tool_result_summary": {"summary": "opened file"},
            },
        }
    }

    clarification_context = builder.build_clarification_context(prior_state)
    response_context = builder.build_response_context(prior_state["memory_state"])

    assert clarification_context["task_snapshot"]["recent_focus"] == ["README.md"]
    assert clarification_context["working_memory_view"]["active_target"] == "README.md"
    assert clarification_context["working_memory_view"]["open_issues"] == ["verification"]
    assert response_context["recent_paths"] == ["README.md", "docs/README.md"]
    assert response_context["confirmed_targets"] == ["README.md"]
    assert response_context["excluded_targets"] == ["docs/README.md"]


def test_workflow_model_context_builder_exposes_recent_failure_diagnoses_and_recovery_modes():
    from agent_runtime_framework.workflow.context.model_context import WorkflowModelContextBuilder
    from agent_runtime_framework.workflow.state.models import new_agent_graph_state

    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="change_and_verify")
    state = new_agent_graph_state(run_id="run-context-recovery", goal_envelope=goal)
    state.aggregated_payload["verification"] = {"status": "failed", "success": False}
    state.failure_history = [
        {
            "iteration": 1,
            "status": "replan",
            "reason": "Verification missing",
            "diagnosis": {"primary_gap": "verification_missing"},
            "failure_diagnosis": {
                "category": "verification_gap",
                "subcategory": "verification_missing",
                "summary": "Verification missing",
                "blocking_issue": "Verification missing",
                "recoverable": True,
                "suggested_recovery_mode": "run_verification",
            },
        }
    ]
    state.recovery_history = [
        {
            "trigger": "replan",
            "action": "replan",
            "recovery_mode": "run_verification",
            "failure_diagnosis": {"category": "verification_gap"},
        }
    ]

    working_view = WorkflowModelContextBuilder().build_working_memory_fragment(state)

    assert working_view["recent_failure_diagnoses"][0]["category"] == "verification_gap"
    assert working_view["recent_recovery_modes"] == ["run_verification"]
    assert working_view["last_verification_result"]["status"] == "failed"


def test_memory_manager_writes_session_and_working_memory():
    from agent_runtime_framework.memory import MemoryManager
    from agent_runtime_framework.workflow.state.models import new_agent_graph_state

    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="file_read")
    state = new_agent_graph_state(run_id="run-memory-update", goal_envelope=goal)
    manager = MemoryManager()

    manager.update_session_memory(
        state.memory_state,
        last_active_target="README.md",
        recent_paths=["README.md", "docs/README.md"],
        last_action_summary="read readme",
        last_clarification={"preferred_path": "README.md"},
    )
    manager.update_working_memory(
        state.memory_state,
        active_target="README.md",
        confirmed_targets=["README.md"],
        excluded_targets=["docs/README.md"],
        current_step="explain readme",
    )

    assert state.memory_state.session_memory.last_active_target == "README.md"
    assert state.memory_state.session_memory.last_clarification == {"preferred_path": "README.md"}
    assert state.memory_state.working_memory.confirmed_targets == ["README.md"]
    assert state.memory_state.working_memory.excluded_targets == ["docs/README.md"]


def test_workflow_prompt_helpers_extract_json_and_build_context_block():
    from agent_runtime_framework.workflow.planning.prompt_utils import (
        build_run_context_block,
        extract_json_block,
        render_workflow_prompt_doc,
    )

    context = SimpleNamespace(
        application_context=SimpleNamespace(
            config={"default_directory": "/tmp/demo"},
            session_memory=SimpleNamespace(
                snapshot=lambda: SimpleNamespace(
                    focused_resources=[SimpleNamespace(location="README.md")]
                )
            ),
            tools=SimpleNamespace(names=lambda: ["read_file", "list_dir"]),
        )
    )
    session = SimpleNamespace(turns=[])

    assert extract_json_block("```json\n{\"ok\": true}\n```") == "{\"ok\": true}"
    assert render_workflow_prompt_doc("conversation_system") == "你是一个简洁友好的中文助手。"
    block = build_run_context_block(context, session=session, user_input="读取 README.md")
    assert "Workspace: /tmp/demo" in block
    assert "User input: 读取 README.md" in block
    assert "Available tools: read_file, list_dir" in block


def test_workflow_planner_prompt_helpers_expose_intent_and_node_taxonomy():
    from agent_runtime_framework.workflow.planning.prompts import (
        build_decomposition_system_prompt,
        build_goal_analysis_system_prompt,
        build_subgraph_planner_system_prompt,
    )

    goal_prompt = build_goal_analysis_system_prompt()
    decomposition_prompt = build_decomposition_system_prompt()
    subgraph_prompt = build_subgraph_planner_system_prompt()

    assert "primary_intent" in goal_prompt
    assert "file_read" in goal_prompt
    assert "target_explainer" in goal_prompt
    assert "change_and_verify" in goal_prompt
    assert "task_profile" in decomposition_prompt
    assert "workspace_discovery" in decomposition_prompt
    assert "capability/recipe-first strategy" in subgraph_prompt
    assert "selected_recipe_id" in subgraph_prompt
    assert "selected_capability_ids" in subgraph_prompt
    assert "preferred_recipe_ids" in subgraph_prompt
