from agent_runtime_framework.workflow import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_PENDING,
    RUN_STATUS_PENDING,
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
    assert state.approval_requested is True
    assert state.approval_granted is False
    assert state.error == ""


def test_workflow_models_expose_stable_status_values():
    node = WorkflowNode(node_id="plan", node_type="planner")
    run = WorkflowRun(goal="plan task")
    result = NodeResult(status=NODE_STATUS_COMPLETED)

    assert RUN_STATUS_PENDING == "pending"
    assert NODE_STATUS_PENDING == "pending"
    assert NODE_STATUS_COMPLETED == "completed"
    assert node.status == NODE_STATUS_PENDING
    assert run.status == RUN_STATUS_PENDING
    assert result.status == NODE_STATUS_COMPLETED



def test_workflow_payload_helpers_normalize_aggregated_schema():
    from agent_runtime_framework.workflow.models import normalize_aggregated_workflow_payload

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

    from agent_runtime_framework.workflow.models import (
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

    payload = serialize_agent_graph_state(state)

    assert payload["run_id"] == "run-1"
    assert payload["goal_envelope"]["goal"] == "总结 agent graph runtime 设计"
    assert payload["aggregated_payload"]["summaries"] == []

    serialized_subgraph = subgraph.as_payload()
    serialized_judge = judge.as_payload()

    assert serialized_subgraph["nodes"][0]["reason"] == "Need primary evidence"
    assert serialized_judge["status"] == "needs_more_evidence"
