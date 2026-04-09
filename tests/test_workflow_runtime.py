from agent_runtime_framework.workflow import (
    InteractionRequest,
    NODE_STATUS_COMPLETED,
    NODE_STATUS_FAILED,
    GoalEnvelope,
    PlannedNode,
    PlannedSubgraph,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_WAITING_APPROVAL,
    RUN_STATUS_WAITING_INPUT,
    NodeResult,
    NodeState,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    new_agent_graph_state,
    normalize_aggregated_workflow_payload,
)
from agent_runtime_framework.workflow.planning.goal_intake import build_goal_envelope
import pytest
from agent_runtime_framework.workflow.runtime.execution import GraphExecutionRuntime
from agent_runtime_framework.workflow.runtime.scheduler import WorkflowScheduler
from agent_runtime_framework.workflow.nodes.interaction import ClarificationExecutor, ToolCallExecutor
from agent_runtime_framework.workflow.orchestration.aggregation import aggregate_node_results
from agent_runtime_framework.workflow.executors.target_resolution import TargetResolutionExecutor
from agent_runtime_framework.workflow.nodes.core import FinalResponseExecutor, VerificationExecutor
from agent_runtime_framework.workflow.nodes.discovery import (
    ChunkedFileReadExecutor,
    ContentSearchExecutor,
    EvidenceSynthesisExecutor,
    WorkspaceDiscoveryExecutor,
)
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.tools.specs import ToolSpec
from types import SimpleNamespace
from agent_runtime_framework.resources import LocalFileResourceRepository


class NoopExecutor:
    def execute(self, node, run, context=None):
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"node": node.node_id})


class FailExecutor:
    def execute(self, node, run, context=None):
        return NodeResult(status=NODE_STATUS_FAILED, error="boom")


class NoDirectExecuteRuntime:
    def __init__(self, executors):
        self.executors = executors
        self.calls: list[list[str]] = []

    def run(self, run):
        run.shared_state.setdefault("node_results", {})
        self.calls.append([node.node_type for node in run.graph.nodes])
        for node in run.graph.nodes:
            executor = self.executors[node.node_type]
            result = executor.execute(node, run, {})
            run.node_states[node.node_id] = NodeState(node_id=node.node_id, status=result.status, result=result, error=result.error)
            run.shared_state["node_results"][node.node_id] = result
            if result.status == RUN_STATUS_FAILED:
                run.status = RUN_STATUS_FAILED
                return run
            if result.interaction_request is not None:
                run.pending_interaction = result.interaction_request
                run.status = RUN_STATUS_WAITING_INPUT
                return run
        run.status = RUN_STATUS_COMPLETED
        return run

    def resume(self, run, *, resume_token, approved):
        raise AssertionError("resume should not be used in this test")

    def _execute(self, executor, node, run):
        raise AssertionError("AgentGraphRuntime should not call _execute directly")


class RecordingGraphExecutionRuntime(GraphExecutionRuntime):
    def __init__(self, executors, context=None):
        super().__init__(executors=executors, context=context or {})
        self.calls: list[list[str]] = []

    def run(self, run):
        self.calls.append([node.node_type for node in run.graph.nodes])
        return super().run(run)


def test_scheduler_only_returns_nodes_with_completed_dependencies():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="first", node_type="noop"),
            WorkflowNode(node_id="second", node_type="noop", dependencies=["first"]),
        ],
        edges=[WorkflowEdge(source="first", target="second")],
    )
    run = WorkflowRun(
        goal="demo",
        graph=graph,
        node_states={
            "first": NodeState(node_id="first"),
            "second": NodeState(node_id="second"),
        },
    )

    ready_before = WorkflowScheduler().ready_nodes(run)
    run.node_states["first"].status = NODE_STATUS_COMPLETED
    ready_after = WorkflowScheduler().ready_nodes(run)

    assert [node.node_id for node in ready_before] == ["first"]
    assert [node.node_id for node in ready_after] == ["second"]


def test_runtime_executes_ready_nodes_in_dependency_order():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="start", node_type="noop"),
            WorkflowNode(node_id="finish", node_type="noop", dependencies=["start"]),
        ],
        edges=[WorkflowEdge(source="start", target="finish")],
    )
    run = WorkflowRun(goal="demo", graph=graph)

    result = GraphExecutionRuntime(executors={"noop": NoopExecutor()}).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["start"].status == NODE_STATUS_COMPLETED
    assert result.node_states["finish"].status == NODE_STATUS_COMPLETED
    assert result.node_states["finish"].result.output == {"node": "finish"}


def test_failed_node_stops_downstream_execution():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="start", node_type="fail"),
            WorkflowNode(node_id="finish", node_type="noop", dependencies=["start"]),
        ],
        edges=[WorkflowEdge(source="start", target="finish")],
    )
    run = WorkflowRun(goal="demo", graph=graph)

    result = GraphExecutionRuntime(
        executors={"fail": FailExecutor(), "noop": NoopExecutor()}
    ).run(run)

    assert result.status == RUN_STATUS_FAILED
    assert result.node_states["start"].status == NODE_STATUS_FAILED
    assert result.node_states["finish"].status != NODE_STATUS_COMPLETED
    assert result.node_states["finish"].result is None


class ApprovalExecutor:
    def execute(self, node, run, context=None):
        return NodeResult(status="waiting_approval", approval_data={"kind": "custom"}, output={"summary": "needs approval"})

    def resume(self, node, run, prior_result, *, approved, context=None):
        if not approved:
            return NodeResult(status=NODE_STATUS_FAILED, error="approval rejected")
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"node": node.node_id, "approved": True})


def test_runtime_resumes_executor_managed_approval_node():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="change", node_type="approval_executor"),
            WorkflowNode(node_id="finish", node_type="noop", dependencies=["change"]),
        ],
        edges=[WorkflowEdge(source="change", target="finish")],
    )
    run = WorkflowRun(goal="demo", graph=graph)
    runtime = GraphExecutionRuntime(executors={"approval_executor": ApprovalExecutor(), "noop": NoopExecutor()})

    first = runtime.run(run)

    assert first.status == RUN_STATUS_WAITING_APPROVAL
    resume_token = first.shared_state["resume_token"]

    resumed = runtime.resume(first, resume_token=resume_token, approved=True)

    assert resumed.status == RUN_STATUS_COMPLETED
    assert resumed.node_states["change"].result.output == {"node": "change", "approved": True}
    assert resumed.node_states["finish"].status == NODE_STATUS_COMPLETED



def test_single_node_workflow_run_initializes_and_completes_state_tracking():
    graph = WorkflowGraph(nodes=[WorkflowNode(node_id="only", node_type="noop")], edges=[])
    run = WorkflowRun(goal="demo", graph=graph)

    result = GraphExecutionRuntime(executors={"noop": NoopExecutor()}).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["only"].status == NODE_STATUS_COMPLETED
    assert result.shared_state["node_results"]["only"].output == {"node": "only"}


def test_runtime_pauses_when_executor_requests_user_input():
    graph = WorkflowGraph(nodes=[WorkflowNode(node_id="clarify", node_type="clarification", metadata={"prompt": "Which README should I use?"})], edges=[])
    run = WorkflowRun(goal="read the readme", graph=graph)

    result = GraphExecutionRuntime(executors={"clarification": ClarificationExecutor()}).run(run)

    assert result.status == RUN_STATUS_WAITING_INPUT
    assert result.pending_interaction is not None
    assert result.pending_interaction.kind == "clarification"
    assert result.pending_interaction.prompt == "Which README should I use?"
    assert result.node_states["clarify"].status == NODE_STATUS_COMPLETED
    assert result.node_states["clarify"].result.interaction_request.prompt == "Which README should I use?"





def _make_workspace_runtime_context(tmp_path):
    from agent_runtime_framework.workflow.context.app_context import ApplicationContext
    from agent_runtime_framework.workflow.workspace import WorkspaceContext, build_default_workspace_tools

    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([tmp_path]),
        config={"default_directory": str(tmp_path)},
    )
    for tool in build_default_workspace_tools():
        app_context.tools.register(tool)
    workspace_context = WorkspaceContext(application_context=app_context)
    return {
        "application_context": app_context,
        "workspace_context": workspace_context,
        "workspace_root": str(tmp_path),
    }


def test_runtime_executes_create_path_node_with_workspace_tool(tmp_path):
    from agent_runtime_framework.workflow.nodes.workspace_write import CreatePathExecutor

    run = WorkflowRun(
        goal="创建 docs/notes.md",
        graph=WorkflowGraph(nodes=[WorkflowNode(node_id="create", node_type="create_path", metadata={"path": "docs/notes.md", "kind": "file", "content": "hello\n"})]),
    )

    result = GraphExecutionRuntime(executors={"create_path": CreatePathExecutor()}, context=_make_workspace_runtime_context(tmp_path)).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["create"].result.output["tool_name"] == "create_workspace_path"
    assert result.node_states["create"].result.output["quality_signals"][0]["progress_contribution"] == "workspace_updated"
    assert (tmp_path / "docs" / "notes.md").read_text(encoding="utf-8") == "hello\n"


def test_target_resolution_executor_prefers_interpreted_target_constraints(tmp_path):
    from agent_runtime_framework.workflow.executors.target_resolution import TargetResolutionExecutor

    (tmp_path / "README.md").write_text("root\n", encoding="utf-8")
    (tmp_path / "frontend-shell").mkdir()
    (tmp_path / "frontend-shell" / "README.md").write_text("frontend\n", encoding="utf-8")
    run = WorkflowRun(
        goal="看 README",
        shared_state={
            "interpreted_target": {
                "preferred_path": "README.md",
                "scope_preference": "workspace_root",
                "exclude_paths": ["frontend-shell/README.md"],
            }
        },
    )

    result = TargetResolutionExecutor().execute(
        WorkflowNode(node_id="resolve", node_type="target_resolution"),
        run,
        context=_make_workspace_runtime_context(tmp_path),
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["resolved_path"] == "README.md"


def test_target_resolution_executor_fails_without_interpreted_target(tmp_path):
    result = TargetResolutionExecutor().execute(
        WorkflowNode(node_id="resolve", node_type="target_resolution"),
        WorkflowRun(goal="看 README", shared_state={}),
        context=_make_workspace_runtime_context(tmp_path),
    )

    assert result.status == NODE_STATUS_FAILED
    assert "interpreted_target" in result.error


def test_content_search_executor_uses_search_plan_queries(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.executors.content_search.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "search summary",
    )
    (tmp_path / "README.md").write_text("agent runtime framework\n", encoding="utf-8")
    run = WorkflowRun(
        goal="随便一句话",
        shared_state={
            "node_results": {},
            "search_plan": {
                "semantic_queries": ["agent runtime"],
                "must_avoid": [],
                "path_bias": ["README.md"],
            },
        },
    )
    node = WorkflowNode(node_id="search", node_type="content_search", metadata={})

    result = ContentSearchExecutor().execute(node, run, context={"workspace_root": str(tmp_path)})

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["artifacts"]["search_terms"] == ["agent runtime"]


def test_content_search_executor_fails_without_search_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.executors.content_search.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "search summary",
    )
    result = ContentSearchExecutor().execute(
        WorkflowNode(node_id="search", node_type="content_search", metadata={}),
        WorkflowRun(goal="随便一句话", shared_state={"node_results": {}}),
        context={"workspace_root": str(tmp_path)},
    )

    assert result.status == NODE_STATUS_FAILED
    assert "search_plan" in result.error


def test_chunked_file_read_executor_uses_read_plan_target_and_region(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.executors.chunked_file_read.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "chunk summary",
    )
    target = tmp_path / "README.md"
    target.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
    run = WorkflowRun(
        goal="读 README",
        shared_state={
            "node_results": {},
            "read_plan": {
                "target_path": "README.md",
                "preferred_regions": ["head"],
            },
        },
    )

    result = ChunkedFileReadExecutor().execute(
        WorkflowNode(node_id="read", node_type="chunked_file_read", metadata={}),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["path"] == "README.md"
    assert result.output["chunks"][0]["start_line"] == 1


def test_chunked_file_read_executor_fails_without_read_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.executors.chunked_file_read.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "chunk summary",
    )
    result = ChunkedFileReadExecutor().execute(
        WorkflowNode(node_id="read", node_type="chunked_file_read", metadata={}),
        WorkflowRun(goal="读 README", shared_state={"node_results": {}}),
        context={"workspace_root": str(tmp_path)},
    )

    assert result.status == NODE_STATUS_FAILED
    assert "read_plan" in result.error


def test_semantic_planning_pipeline_resolves_root_readme_after_clarification(monkeypatch, tmp_path):
    from agent_runtime_framework.workflow.nodes.semantic import InterpretTargetExecutor, PlanReadExecutor, PlanSearchExecutor

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.executors.content_search.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "search summary",
    )
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.executors.chunked_file_read.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "read summary",
    )

    def _fake_semantic_plan(context, payload, system_prompt, max_tokens=400):
        if "target request" in system_prompt:
            return {
                "target_kind": "file",
                "preferred_path": "README.md",
                "scope_preference": "workspace_root",
                "exclude_paths": ["frontend-shell/README.md"],
                "confirmed": True,
                "confidence": 0.96,
                "rationale": "clarification indicates the outermost workspace README",
            }
        if "search strategy" in system_prompt:
            return {
                "search_goal": "find the root readme",
                "semantic_queries": ["README.md", "project overview"],
                "must_avoid": ["frontend-shell"],
                "path_bias": ["README.md"],
                "confidence": 0.88,
                "rationale": "search only the root readme candidate",
            }
        return {
            "read_goal": "summarize the project overview",
            "target_path": "README.md",
            "preferred_regions": ["head"],
            "confidence": 0.9,
            "rationale": "the introduction is likely near the top of the file",
        }

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        _fake_semantic_plan,
    )

    (tmp_path / "README.md").write_text("Project overview\nMore details\n", encoding="utf-8")
    (tmp_path / "frontend-shell").mkdir()
    (tmp_path / "frontend-shell" / "README.md").write_text("Frontend shell overview\n", encoding="utf-8")
    runtime_context = _make_workspace_runtime_context(tmp_path)
    run = WorkflowRun(
        goal="我需要你帮我看一下当前项目根目录当中的README在讲什么内容呢？",
        pending_interaction=InteractionRequest(kind="clarification", prompt="Which README?", items=["README.md", "frontend-shell/README.md"]),
        shared_state={
            "clarification_response": "当前工作区的README文件，在最外层的",
            "node_results": {},
        },
    )

    interpret_result = InterpretTargetExecutor().execute(
        WorkflowNode(node_id="interpret", node_type="interpret_target", metadata={"target_hints": ["README"]}),
        run,
        context=runtime_context,
    )
    run.shared_state["node_results"]["interpret"] = interpret_result

    resolve_result = TargetResolutionExecutor().execute(
        WorkflowNode(node_id="resolve", node_type="target_resolution"),
        run,
        context=runtime_context,
    )
    run.shared_state["node_results"]["resolve"] = resolve_result

    search_plan_result = PlanSearchExecutor().execute(
        WorkflowNode(node_id="search_plan", node_type="plan_search"),
        run,
        context=runtime_context,
    )
    run.shared_state["node_results"]["search_plan"] = search_plan_result

    search_result = ContentSearchExecutor().execute(
        WorkflowNode(node_id="search", node_type="content_search"),
        run,
        context={"workspace_root": str(tmp_path)},
    )
    run.shared_state["node_results"]["search"] = search_result

    read_plan_result = PlanReadExecutor().execute(
        WorkflowNode(node_id="read_plan", node_type="plan_read"),
        run,
        context=runtime_context,
    )
    run.shared_state["node_results"]["read_plan"] = read_plan_result

    read_result = ChunkedFileReadExecutor().execute(
        WorkflowNode(node_id="read", node_type="chunked_file_read"),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    assert run.shared_state["interpreted_target"]["preferred_path"] == "README.md"
    assert resolve_result.output["resolved_path"] == "README.md"
    assert search_result.references == [str(tmp_path / "README.md")]
    assert read_result.output["path"] == "README.md"
    assert "Project overview" in read_result.output["chunks"][0]["text"]


def test_runtime_executes_move_path_node_with_workspace_tool(tmp_path):
    from agent_runtime_framework.workflow.nodes.workspace_write import MovePathExecutor

    source = tmp_path / "docs" / "notes.md"
    source.parent.mkdir(parents=True)
    source.write_text("hello\n", encoding="utf-8")
    run = WorkflowRun(
        goal="移动 docs/notes.md",
        graph=WorkflowGraph(nodes=[WorkflowNode(node_id="move", node_type="move_path", metadata={"path": "docs/notes.md", "destination_path": "docs/archive/notes.md"})]),
    )

    result = GraphExecutionRuntime(executors={"move_path": MovePathExecutor()}, context=_make_workspace_runtime_context(tmp_path)).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["move"].result.output["tool_name"] == "move_workspace_path"
    assert not source.exists()
    assert (tmp_path / "docs" / "archive" / "notes.md").read_text(encoding="utf-8") == "hello\n"


def test_runtime_executes_delete_path_node_with_workspace_tool(tmp_path):
    from agent_runtime_framework.workflow.nodes.workspace_write import DeletePathExecutor

    target = tmp_path / "docs" / "notes.md"
    target.parent.mkdir(parents=True)
    target.write_text("hello\n", encoding="utf-8")
    run = WorkflowRun(
        goal="删除 docs/notes.md",
        graph=WorkflowGraph(nodes=[WorkflowNode(node_id="delete", node_type="delete_path", metadata={"path": "docs/notes.md"})]),
    )

    result = GraphExecutionRuntime(executors={"delete_path": DeletePathExecutor()}, context=_make_workspace_runtime_context(tmp_path)).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["delete"].result.output["tool_name"] == "delete_workspace_path"
    assert not target.exists()


def test_runtime_executes_apply_patch_node_with_workspace_tool(tmp_path):
    from agent_runtime_framework.workflow.nodes.workspace_write import ApplyPatchExecutor

    target = tmp_path / "README.md"
    target.write_text("before line\n", encoding="utf-8")
    run = WorkflowRun(
        goal="把 before 改成 after",
        graph=WorkflowGraph(
            nodes=[
                WorkflowNode(
                    node_id="patch",
                    node_type="apply_patch",
                    metadata={"path": "README.md", "search_text": "before", "replace_text": "after"},
                )
            ]
        ),
    )

    result = GraphExecutionRuntime(executors={"apply_patch": ApplyPatchExecutor()}, context=_make_workspace_runtime_context(tmp_path)).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["patch"].result.output["tool_name"] == "apply_text_patch"
    assert target.read_text(encoding="utf-8") == "after line\n"


def test_runtime_executes_write_file_node_with_workspace_tool(tmp_path):
    from agent_runtime_framework.workflow.nodes.workspace_write import WriteFileExecutor

    target = tmp_path / "README.md"
    target.write_text("old\n", encoding="utf-8")
    run = WorkflowRun(
        goal="重写 README.md",
        graph=WorkflowGraph(
            nodes=[
                WorkflowNode(
                    node_id="write",
                    node_type="write_file",
                    metadata={"path": "README.md", "content": "new body\n"},
                )
            ]
        ),
    )

    result = GraphExecutionRuntime(executors={"write_file": WriteFileExecutor()}, context=_make_workspace_runtime_context(tmp_path)).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["write"].result.output["tool_name"] == "edit_workspace_text"
    assert target.read_text(encoding="utf-8") == "new body\n"


def test_runtime_executes_append_text_node_with_workspace_tool(tmp_path):
    from agent_runtime_framework.workflow.nodes.workspace_write import AppendTextExecutor

    target = tmp_path / "README.md"
    target.write_text("line one\n", encoding="utf-8")
    run = WorkflowRun(
        goal="追加内容到 README.md",
        graph=WorkflowGraph(
            nodes=[
                WorkflowNode(
                    node_id="append",
                    node_type="append_text",
                    metadata={"path": "README.md", "content": "line two\n"},
                )
            ]
        ),
    )

    result = GraphExecutionRuntime(executors={"append_text": AppendTextExecutor()}, context=_make_workspace_runtime_context(tmp_path)).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["append"].result.output["tool_name"] == "append_workspace_text"
    assert target.read_text(encoding="utf-8") == "line one\nline two\n"


def test_runtime_preserves_verification_payload_after_graph_native_modify():
    verification = VerificationExecutor().execute(
        WorkflowNode(node_id="verification", node_type="verification"),
        WorkflowRun(
            goal="modify and verify",
            shared_state={
                "node_results": {
                    "write": NodeResult(
                        status=NODE_STATUS_COMPLETED,
                        output={
                            "summary": "modified file",
                            "verification_events": [
                                {"status": "passed", "success": True, "summary": "verified after modify", "verification_type": "post_modify"}
                            ],
                        },
                    )
                }
            },
        ),
    )

    assert verification.output["verification"]["status"] == "passed"
    assert verification.output["verification_by_type"]["post_modify"]["status"] == "passed"


def test_aggregate_node_results_preserves_structured_output_fields():
    aggregated = aggregate_node_results(
        [
            NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summary": "workspace summary",
                    "facts": [{"kind": "entrypoint", "path": "README.md"}],
                    "evidence_items": [{"kind": "path", "path": "README.md", "summary": "README"}],
                    "artifacts": {"tree_sample": ["README.md"]},
                    "open_questions": ["missing test root"],
                    "verification": {"status": "not_run", "summary": "not verified"},
                    "quality_signals": [{"source": "workspace_discovery", "relevance": "medium", "confidence": 0.5}],
                    "reasoning_trace": [{"kind": "observation", "summary": "workspace root detected"}],
                    "conflicts": ["README path not confirmed"],
                },
                references=["README.md"],
            ),
            NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summary": "code summary",
                    "facts": [{"kind": "source_root", "path": "src"}],
                    "evidence_items": [{"kind": "path", "path": "src", "summary": "Source root"}],
                    "artifacts": {"matched_paths": ["src/app.py"]},
                    "open_questions": ["need config path"],
                    "quality_signals": [{"source": "content_search", "relevance": "high", "confidence": 0.8}],
                    "reasoning_trace": [{"kind": "selection", "summary": "src looks like source root"}],
                },
                references=["src"],
            ),
        ]
    )

    assert aggregated.output == {
        "summaries": ["workspace summary", "code summary"],
        "facts": [
            {"kind": "entrypoint", "path": "README.md"},
            {"kind": "source_root", "path": "src"},
        ],
        "evidence_items": [
            {"kind": "path", "path": "README.md", "summary": "README"},
            {"kind": "path", "path": "src", "summary": "Source root"},
        ],
        "chunks": [],
        "artifacts": {
            "tree_sample": ["README.md"],
            "matched_paths": ["src/app.py"],
        },
        "open_questions": ["missing test root", "need config path"],
        "verification": {"status": "not_run", "summary": "not verified"},
        "verification_events": [{"status": "not_run", "summary": "not verified"}],
        "quality_signals": [
            {"source": "workspace_discovery", "relevance": "medium", "confidence": 0.5},
            {"source": "content_search", "relevance": "high", "confidence": 0.8},
        ],
        "reasoning_trace": [
            {"kind": "observation", "summary": "workspace root detected"},
            {"kind": "selection", "summary": "src looks like source root"},
        ],
        "conflicts": ["README path not confirmed"],
    }
    assert aggregated.references == ["README.md", "src"]


def test_content_search_executor_emits_quality_signals(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.executors.content_search.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "search summary",
    )
    target = tmp_path / "README.md"
    target.write_text("agent graph runtime\nplanner\njudge\n", encoding="utf-8")
    run = WorkflowRun(
        goal="读取 README.md",
        shared_state={
            "node_results": {},
            "search_plan": {
                "search_goal": "find readme",
                "semantic_queries": ["agent graph"],
                "must_avoid": [],
                "path_bias": ["README.md"],
            },
        },
    )
    node = WorkflowNode(node_id="search", node_type="content_search", metadata={})

    result = ContentSearchExecutor().execute(node, run, context={"workspace_root": str(tmp_path)})

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["quality_signals"][0]["progress_contribution"] == "candidate_identified"
    assert result.output["quality_signals"][0]["recoverable_error"] is False
    assert result.output["reasoning_trace"][0]["kind"] == "search_strategy"


def test_chunked_file_read_executor_emits_quality_signals(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.executors.chunked_file_read.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "chunk summary",
    )
    target = tmp_path / "README.md"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    run = WorkflowRun(
        goal="读取 README.md",
        shared_state={
            "node_results": {},
            "read_plan": {
                "target_path": "README.md",
                "preferred_regions": ["head"],
            },
        },
    )
    node = WorkflowNode(node_id="read", node_type="chunked_file_read", metadata={})

    result = ChunkedFileReadExecutor().execute(node, run, context={"workspace_root": str(tmp_path)})

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["quality_signals"][0]["progress_contribution"] == "grounded_evidence_collected"
    assert result.output["quality_signals"][0]["verification_needed"] is False
    assert result.output["reasoning_trace"][0]["kind"] == "read_strategy"


def test_interpret_target_executor_stores_interpreted_target(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import InterpretTargetExecutor

    seen_payload = {}

    def _fake_plan(context, payload, system_prompt, max_tokens=400):
        seen_payload["payload"] = payload
        return {
            "target_kind": "file",
            "preferred_path": "README.md",
            "scope_preference": "workspace_root",
            "exclude_paths": ["frontend-shell/README.md"],
            "confirmed": True,
            "confidence": 0.93,
            "rationale": "user clarified the outermost readme",
        }

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        _fake_plan,
    )
    run = WorkflowRun(
        goal="看根目录 README",
        pending_interaction=InteractionRequest(kind="clarification", prompt="Which README?", items=["README.md", "frontend-shell/README.md"]),
        shared_state={
            "clarification_response": "最外层那个 README",
            "failure_history": [{"iteration": 1, "status": "needs_clarification"}],
        },
    )

    result = InterpretTargetExecutor().execute(WorkflowNode(node_id="interpret", node_type="interpret_target"), run, context={})

    assert result.status == NODE_STATUS_COMPLETED
    assert run.shared_state["interpreted_target"]["preferred_path"] == "README.md"
    assert result.output["quality_signals"][0]["progress_contribution"] == "target_constraints_defined"
    assert seen_payload["payload"]["prior_candidates"] == ["README.md", "frontend-shell/README.md"]
    assert seen_payload["payload"]["failure_history"][0]["status"] == "needs_clarification"
    assert run.shared_state["memory_state"]["semantic_memory"]["interpreted_target"]["preferred_path"] == "README.md"


def test_interpret_target_executor_requires_confirmed_and_preferred_path(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import InterpretTargetExecutor

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        lambda context, payload, system_prompt, max_tokens=400: {
            "target_kind": "file",
            "scope_preference": "workspace_root",
            "exclude_paths": [],
            "confidence": 0.9,
        },
    )
    run = WorkflowRun(goal="看根目录 README", shared_state={})

    with pytest.raises(ValueError, match="preferred_path"):
        InterpretTargetExecutor().execute(WorkflowNode(node_id="interpret", node_type="interpret_target"), run, context={})


def test_plan_search_executor_stores_search_plan(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import PlanSearchExecutor

    seen_payload = {}

    def _fake_plan(context, payload, system_prompt, max_tokens=400):
        seen_payload["payload"] = payload
        return {
            "search_goal": "find backend readme",
            "semantic_queries": ["README", "agent runtime"],
            "must_avoid": ["frontend-shell"],
            "path_bias": ["README.md"],
            "confidence": 0.81,
        }

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        _fake_plan,
    )
    run = WorkflowRun(
        goal="看根目录 README",
        shared_state={
            "interpreted_target": {"preferred_path": "README.md"},
            "failure_history": [{"iteration": 2, "status": "needs_more_evidence"}],
            "attempted_strategies": ["search readme broadly"],
        },
    )

    result = PlanSearchExecutor().execute(WorkflowNode(node_id="search_plan", node_type="plan_search"), run, context={})

    assert result.status == NODE_STATUS_COMPLETED
    assert run.shared_state["search_plan"]["semantic_queries"] == ["README", "agent runtime"]
    assert result.output["reasoning_trace"][0]["kind"] == "search_plan"
    assert seen_payload["payload"]["attempted_strategies"] == ["search readme broadly"]
    assert run.shared_state["memory_state"]["semantic_memory"]["search_plan"]["semantic_queries"] == ["README", "agent runtime"]


def test_plan_search_executor_requires_semantic_queries(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import PlanSearchExecutor

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        lambda context, payload, system_prompt, max_tokens=400: {
            "search_goal": "find backend readme",
            "path_bias": ["README.md"],
            "must_avoid": [],
            "confidence": 0.81,
        },
    )
    run = WorkflowRun(goal="看根目录 README", shared_state={"interpreted_target": {"preferred_path": "README.md"}})

    with pytest.raises(ValueError, match="semantic_queries"):
        PlanSearchExecutor().execute(WorkflowNode(node_id="search_plan", node_type="plan_search"), run, context={})


def test_plan_read_executor_stores_read_plan(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import PlanReadExecutor

    seen_payload = {}

    def _fake_plan(context, payload, system_prompt, max_tokens=400):
        seen_payload["payload"] = payload
        return {
            "read_goal": "summarize project overview",
            "target_path": "README.md",
            "preferred_regions": ["head"],
            "confidence": 0.84,
        }

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        _fake_plan,
    )
    run = WorkflowRun(
        goal="看根目录 README",
        shared_state={
            "interpreted_target": {"preferred_path": "README.md"},
            "search_plan": {"search_goal": "find backend readme"},
            "failure_history": [{"iteration": 3, "status": "needs_more_evidence"}],
        },
    )

    result = PlanReadExecutor().execute(WorkflowNode(node_id="read_plan", node_type="plan_read"), run, context={})

    assert result.status == NODE_STATUS_COMPLETED
    assert run.shared_state["read_plan"]["preferred_regions"] == ["head"]
    assert result.output["quality_signals"][0]["progress_contribution"] == "read_strategy_defined"
    assert seen_payload["payload"]["search_plan"]["search_goal"] == "find backend readme"
    assert run.shared_state["memory_state"]["semantic_memory"]["read_plan"]["target_path"] == "README.md"


def test_plan_read_executor_requires_target_path(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import PlanReadExecutor

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        lambda context, payload, system_prompt, max_tokens=400: {
            "read_goal": "summarize project overview",
            "preferred_regions": ["head"],
            "confidence": 0.84,
        },
    )
    run = WorkflowRun(
        goal="看根目录 README",
        shared_state={
            "interpreted_target": {"preferred_path": "README.md"},
            "search_plan": {"search_goal": "find backend readme"},
        },
    )

    with pytest.raises(ValueError, match="target_path"):
        PlanReadExecutor().execute(WorkflowNode(node_id="read_plan", node_type="plan_read"), run, context={})


def test_plan_search_executor_rejects_partial_model_output(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import PlanSearchExecutor

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        lambda context, payload, system_prompt, max_tokens=400: {
            "semantic_queries": "README.md",
            "path_bias": "README.md",
            "must_avoid": None,
        },
    )
    run = WorkflowRun(goal="看 README", shared_state={"interpreted_target": {"preferred_path": "README.md"}})

    with pytest.raises(ValueError, match="search_goal"):
        PlanSearchExecutor().execute(WorkflowNode(node_id="search_plan", node_type="plan_search"), run, context={})


def test_plan_search_executor_repairs_partial_model_output(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import PlanSearchExecutor

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        lambda context, payload, system_prompt, max_tokens=400: {
            "semantic_queries": "README.md",
            "path_bias": "README.md",
        },
    )
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic.repair_structured_output",
        lambda *args, **kwargs: {
            "search_goal": "find the root readme",
            "semantic_queries": ["README.md"],
            "path_bias": ["README.md"],
        },
    )
    run = WorkflowRun(goal="看 README", shared_state={"interpreted_target": {"preferred_path": "README.md"}})

    result = PlanSearchExecutor().execute(WorkflowNode(node_id="search_plan", node_type="plan_search"), run, context={})

    assert result.status == NODE_STATUS_COMPLETED
    assert run.shared_state["search_plan"]["search_goal"] == "find the root readme"


def test_plan_read_executor_rejects_partial_model_output(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import PlanReadExecutor

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        lambda context, payload, system_prompt, max_tokens=400: {
            "target_path": "README.md",
            "preferred_regions": "head",
            "confidence": "0.6",
        },
    )
    run = WorkflowRun(goal="看 README", shared_state={"interpreted_target": {"preferred_path": "README.md"}})

    with pytest.raises(ValueError, match="read_goal"):
        PlanReadExecutor().execute(WorkflowNode(node_id="read_plan", node_type="plan_read"), run, context={})


def test_plan_read_executor_repairs_partial_model_output(monkeypatch):
    from agent_runtime_framework.workflow.nodes.semantic import PlanReadExecutor

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic._structured_semantic_plan",
        lambda context, payload, system_prompt, max_tokens=400: {
            "target_path": "README.md",
            "preferred_regions": "head",
            "confidence": "0.6",
        },
    )
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.semantic.repair_structured_output",
        lambda *args, **kwargs: {
            "read_goal": "summarize the readme",
            "target_path": "README.md",
            "preferred_regions": ["head"],
            "confidence": 0.6,
        },
    )
    run = WorkflowRun(goal="看 README", shared_state={"interpreted_target": {"preferred_path": "README.md"}, "search_plan": {"search_goal": "find readme"}})

    result = PlanReadExecutor().execute(WorkflowNode(node_id="read_plan", node_type="plan_read"), run, context={})

    assert result.status == NODE_STATUS_COMPLETED
    assert run.shared_state["read_plan"]["read_goal"] == "summarize the readme"


def test_repair_structured_output_retries_three_times(monkeypatch):
    from agent_runtime_framework.workflow.llm.structured_output_repair import repair_structured_output

    attempts: list[int] = []

    def _fake_attempt(context, *, role, system_prompt, payload, max_tokens=500):
        attempts.append(int(payload["attempt"]))
        if int(payload["attempt"]) < 3:
            return None, "invalid json"
        return {"status": "replan", "reason": "ok", "allowed_next_node_types": ["plan_read"]}, None

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.llm.structured_output_repair._repair_attempt",
        _fake_attempt,
    )

    repaired = repair_structured_output(
        context={},
        role="judge",
        contract_kind="judge_contract",
        required_fields=["status", "reason", "allowed_next_node_types"],
        original_output="not json",
        validation_error="JSONDecodeError",
        request_payload={"goal": "demo"},
    )

    assert repaired == {"status": "replan", "reason": "ok", "allowed_next_node_types": ["plan_read"]}
    assert attempts == [1, 2, 3]


def test_aggregate_node_results_deduplicates_facts_evidence_and_references():
    shared_fact = {"kind": "entrypoint", "path": "README.md"}
    shared_evidence = {"kind": "path", "path": "README.md", "summary": "README"}

    aggregated = aggregate_node_results(
        [
            NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summary": "first",
                    "facts": [shared_fact],
                    "evidence_items": [shared_evidence],
                },
                references=["README.md"],
            ),
            NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summary": "second",
                    "facts": [shared_fact],
                    "evidence_items": [shared_evidence],
                },
                references=["README.md"],
            ),
        ]
    )

    assert aggregated.output["facts"] == [shared_fact]
    assert aggregated.output["evidence_items"] == [shared_evidence]
    assert aggregated.references == ["README.md"]


def test_verification_executor_returns_failed_if_any_upstream_verification_fails():
    run = WorkflowRun(
        goal="verify",
        shared_state={
            "node_results": {
                "aggregate_results": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "summary": "aggregate",
                        "verification_events": [
                            {"status": "passed", "success": True, "summary": "lint ok"},
                            {"status": "failed", "success": False, "summary": "tests failed"},
                        ],
                    },
                    references=["README.md"],
                )
            }
        },
    )

    result = VerificationExecutor().execute(
        WorkflowNode(node_id="verification", node_type="verification"),
        run,
    )

    assert result.status == NODE_STATUS_FAILED
    assert result.output["verification"] == {
        "status": "failed",
        "success": False,
        "summary": "tests failed",
    }
    assert result.error == "tests failed"


def test_verification_executor_returns_passed_only_for_explicit_success_events():
    run = WorkflowRun(
        goal="verify",
        shared_state={
            "node_results": {
                "aggregate_results": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "summary": "aggregate",
                        "verification_events": [
                            {"status": "passed", "success": True, "summary": "lint ok"},
                            {"status": "passed", "success": True, "summary": "tests ok"},
                        ],
                    },
                    references=["README.md"],
                )
            }
        },
    )

    result = VerificationExecutor().execute(
        WorkflowNode(node_id="verification", node_type="verification"),
        run,
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["verification"] == {
        "status": "passed",
        "success": True,
        "summary": "lint ok；tests ok",
    }
    assert result.output["quality_signals"][0]["progress_contribution"] == "verification_completed"
    assert result.output["reasoning_trace"][0]["kind"] == "verification_summary"


def test_verification_executor_actively_verifies_post_write_nodes(tmp_path):
    target = tmp_path / "tet.txt"
    target.write_text("鳄鱼通常生活在河流、湖泊和沼泽地带。", encoding="utf-8")
    run = WorkflowRun(
        goal="创建 tet.txt 并写入鳄鱼习性",
        shared_state={
            "node_results": {
                "write_file": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "tool_name": "edit_workspace_text",
                        "arguments": {"path": "tet.txt", "content": "鳄鱼通常生活在河流、湖泊和沼泽地带。"},
                        "tool_output": {
                            "path": "tet.txt",
                            "changed_paths": ["tet.txt"],
                            "summary": "Edited tet.txt",
                        },
                        "summary": "Edited tet.txt",
                    },
                    references=["tet.txt"],
                )
            }
        },
    )

    result = VerificationExecutor().execute(
        WorkflowNode(node_id="verification", node_type="verification", metadata={"verification_type": "post_write"}),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["verification"]["status"] == "passed"
    assert result.output["verification_by_type"]["post_write"]["status"] == "passed"


def test_verification_executor_does_not_block_read_only_workflows():
    run = WorkflowRun(
        goal="解释 README.md",
        shared_state={
            "node_results": {
                "chunked_file_read": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "summary": "README explains the runtime architecture",
                        "facts": [{"kind": "file", "path": "README.md"}],
                        "evidence_items": [{"kind": "path", "path": "README.md", "summary": "Project overview"}],
                    },
                    references=["README.md"],
                )
            }
        },
    )

    result = VerificationExecutor().execute(
        WorkflowNode(node_id="verification", node_type="verification"),
        run,
        context={"workspace_root": "."},
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["verification"]["status"] == "passed"
    assert result.output["verification"]["success"] is True


def test_final_response_executor_prefers_evidence_synthesis_output():
    run = WorkflowRun(
        goal="解释仓库",
        shared_state={
            "aggregated_result": NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={"summaries": ["aggregate summary"]},
                references=["README.md"],
            ),
            "evidence_synthesis": {"summary": "evidence summary", "facts": [{"kind": "source_root", "path": "src"}]},
        },
    )

    result = FinalResponseExecutor().execute(
        WorkflowNode(node_id="final_response", node_type="final_response"),
        run,
    )

    assert result.output["final_response"] == "evidence summary"





def test_append_subgraph_appends_nodes_in_order_after_anchor():
    from agent_runtime_framework.workflow.planning.graph_mutation import append_subgraph

    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="plan_1", node_type="plan"),
            WorkflowNode(node_id="judge_1", node_type="judge", dependencies=["plan_1"]),
        ],
        edges=[WorkflowEdge(source="plan_1", target="judge_1")],
    )
    subgraph = PlannedSubgraph(
        iteration=1,
        planner_summary="collect evidence",
        nodes=[
            PlannedNode(node_id="content_search_1", node_type="content_search", reason="locate file", success_criteria=["find file"]),
            PlannedNode(node_id="chunked_file_read_1", node_type="chunked_file_read", reason="read file", depends_on=["content_search_1"], success_criteria=["read file"]),
        ],
        edges=[WorkflowEdge(source="content_search_1", target="chunked_file_read_1")],
        metadata={"parent_judge_id": "judge_1"},
    )

    appended = append_subgraph(graph, subgraph, after_node_id="judge_1")

    assert [node.node_id for node in appended.nodes] == ["plan_1", "judge_1", "content_search_1", "chunked_file_read_1"]


def test_append_subgraph_connects_anchor_and_records_history():
    from agent_runtime_framework.workflow.planning.graph_mutation import append_subgraph

    graph = WorkflowGraph(nodes=[WorkflowNode(node_id="judge_1", node_type="judge")], edges=[])
    subgraph = PlannedSubgraph(
        iteration=2,
        planner_summary="collect verification",
        nodes=[PlannedNode(node_id="verification_2", node_type="verification_step", reason="verify answer", success_criteria=["run verification"])],
        edges=[],
        metadata={"parent_judge_id": "judge_1"},
    )

    appended = append_subgraph(graph, subgraph, after_node_id="judge_1")

    assert ("judge_1", "verification_2") in {(edge.source, edge.target) for edge in appended.edges}
    assert appended.metadata["append_history"][0]["iteration"] == 2
    assert appended.metadata["append_history"][0]["parent_judge_id"] == "judge_1"


def test_append_subgraph_rejects_duplicate_node_id():
    from agent_runtime_framework.workflow.planning.graph_mutation import append_subgraph

    graph = WorkflowGraph(nodes=[WorkflowNode(node_id="judge_1", node_type="judge")], edges=[])
    subgraph = PlannedSubgraph(
        iteration=1,
        planner_summary="duplicate",
        nodes=[PlannedNode(node_id="judge_1", node_type="content_search", reason="bad", success_criteria=["n/a"])],
        edges=[],
    )

    with pytest.raises(ValueError, match="duplicate"):
        append_subgraph(graph, subgraph, after_node_id="judge_1")


def test_append_subgraph_metadata_contains_iteration_and_parent_judge():
    from agent_runtime_framework.workflow.planning.graph_mutation import append_subgraph

    graph = WorkflowGraph(nodes=[WorkflowNode(node_id="judge_2", node_type="judge")], edges=[])
    subgraph = PlannedSubgraph(
        iteration=3,
        planner_summary="more evidence",
        nodes=[PlannedNode(node_id="workspace_discovery_3", node_type="workspace_discovery", reason="inspect workspace", success_criteria=["list files"])],
        edges=[],
        metadata={"parent_judge_id": "judge_2"},
    )

    appended = append_subgraph(graph, subgraph, after_node_id="judge_2")
    history = appended.metadata["append_history"][0]

    assert history["iteration"] == 3
    assert history["parent_judge_id"] == "judge_2"
    assert history["appended_node_ids"] == ["workspace_discovery_3"]


def test_agent_graph_runtime_completes_when_first_iteration_is_accepted():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        success_criteria=["read file"],
    )
    planned = PlannedSubgraph(
        iteration=1,
        planner_summary="read file",
        nodes=[PlannedNode(node_id="content_search_1", node_type="content_search", reason="locate file", success_criteria=["find file"])],
        edges=[],
        metadata={"parent_judge_id": "plan_1"},
    )
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"content_search": NoopExecutor()}),
        planner=lambda goal_envelope, state, context: planned,
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "accepted", "reason": "enough"},
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.final_output == "enough"
    assert result.metadata["agent_graph_state"]["current_iteration"] == 1
    assert len(result.metadata["agent_graph_state"]["planned_subgraphs"]) == 1
    assert "append_history" not in result.metadata
    node_ids = [node.node_id for node in result.graph.nodes]
    assert node_ids[:3] == ["goal_intake", "context_assembly", "plan_1"]
    assert "aggregate_results_1" in node_ids
    assert "evidence_synthesis_1" in node_ids
    assert "judge_1" in node_ids


def test_agent_graph_runtime_appends_second_iteration_when_more_evidence_is_needed():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(
        goal="总结 docs",
        normalized_goal="总结 docs",
        intent="compound",
        success_criteria=["collect evidence"],
    )
    planner_calls = []

    def _planner(_goal, state, _context):
        planner_calls.append(state.current_iteration)
        iteration = state.current_iteration + 1
        return PlannedSubgraph(
            iteration=iteration,
            planner_summary=f"iteration {iteration}",
            nodes=[PlannedNode(node_id=f"workspace_subtask_{iteration}", node_type="workspace_subtask", reason="collect more", success_criteria=["progress"])],
            edges=[],
            metadata={"parent_judge_id": f"judge_{iteration - 1}" if iteration > 1 else "plan_1"},
        )

    def _judge(_goal, _aggregated_payload, state):
        if state.current_iteration < 2:
            return {"status": "needs_more_evidence", "reason": "keep going"}
        return {"status": "accepted", "reason": "done"}

    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor()}),
        planner=_planner,
        judge=_judge,
        max_iterations=3,
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.final_output == "done"
    assert result.metadata["agent_graph_state"]["current_iteration"] == 2
    assert len(result.metadata["agent_graph_state"]["planned_subgraphs"]) == 2
    assert len(result.graph.metadata["append_history"]) == 2


def test_agent_graph_runtime_returns_limited_answer_when_iteration_budget_is_exhausted():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(
        goal="长任务",
        normalized_goal="长任务",
        intent="compound",
        success_criteria=["finish within budget"],
    )
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=state.current_iteration + 1,
            planner_summary="still working",
            nodes=[PlannedNode(node_id=f"workspace_subtask_{state.current_iteration + 1}", node_type="workspace_subtask", reason="continue", success_criteria=["progress"])],
            edges=[],
            metadata={"parent_judge_id": "plan_1"},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "needs_more_evidence", "reason": "not enough yet", "missing_evidence": ["final verification"]},
        max_iterations=1,
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_COMPLETED
    assert "Iteration limit reached" in result.final_output
    assert "final verification" in result.final_output


def test_judge_progress_accepts_when_model_returns_accept(monkeypatch):
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {
            "status": "accept",
            "reason": "Collected sufficient evidence",
            "allowed_next_node_types": ["final_response"],
        },
    )

    goal = GoalEnvelope(goal="读取 README.md", normalized_goal="读取 README.md", intent="file_read", success_criteria=["read file"])
    decision = judge_progress(
        goal,
        {
            "summaries": ["readme summary"],
            "facts": [{"kind": "file", "path": "README.md"}],
            "evidence_items": [{"kind": "path", "path": "README.md"}],
            "chunks": [{"path": "README.md", "start_line": 1, "end_line": 3}],
            "artifacts": {},
            "open_questions": [],
            "verification": {"status": "passed", "success": True, "summary": "verified"},
            "verification_events": [{"status": "passed", "success": True, "summary": "verified", "verification_type": "evidence"}],
        },
        new_agent_graph_state(run_id="judge-1", goal_envelope=goal),
    )

    assert decision.status == "accepted"


def test_judge_progress_repairs_invalid_model_contract(monkeypatch):
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {"status": "maybe", "reason": ""},
    )
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.repair_structured_output",
        lambda *args, **kwargs: {
            "status": "replan",
            "reason": "Need a direct file read before answering.",
            "allowed_next_node_types": ["plan_read", "chunked_file_read"],
            "blocked_next_node_types": ["final_response"],
        },
    )

    goal = GoalEnvelope(goal="解释根目录 README", normalized_goal="解释根目录 README", intent="file_read")
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload({"summaries": ["candidate only"]}),
        new_agent_graph_state(run_id="judge-repair-1", goal_envelope=goal),
        context={},
    )

    assert decision.status == "replan"
    assert "plan_read" in decision.allowed_next_node_types


def test_judge_progress_tolerates_non_mapping_optional_sections(monkeypatch):
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {
            "status": "replan",
            "reason": "Need grounded evidence first.",
            "coverage_report": ["candidate_only"],
            "replan_hint": "read the target file",
            "diagnosis": ["grounded_evidence_missing"],
            "strategy_guidance": "gather_grounded_evidence",
            "allowed_next_node_types": ["plan_read"],
        },
    )

    goal = GoalEnvelope(goal="解释根目录 README", normalized_goal="解释根目录 README", intent="file_read")
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload({"summaries": ["candidate only"]}),
        new_agent_graph_state(run_id="judge-non-mapping-1", goal_envelope=goal),
    )

    assert decision.status == "replan"
    assert decision.coverage_report == {}
    assert decision.replan_hint == {}
    assert decision.diagnosis == {}
    assert decision.strategy_guidance == {}
    assert decision.allowed_next_node_types == ["plan_read"]


def test_judge_progress_uses_model_replan_for_thin_payload(monkeypatch):
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {
            "status": "replan",
            "reason": "Need grounded evidence first.",
            "diagnosis": {"primary_gap": "grounded_evidence_missing", "goal_status": "insufficient_coverage"},
            "strategy_guidance": {"recommended_strategy": "gather_grounded_evidence"},
            "allowed_next_node_types": ["content_search", "plan_read"],
            "blocked_next_node_types": ["final_response"],
        },
    )

    goal = GoalEnvelope(goal="总结 docs", normalized_goal="总结 docs", intent="compound", success_criteria=["collect evidence"])
    decision = judge_progress(goal, normalize_aggregated_workflow_payload({}), new_agent_graph_state(run_id="judge-2", goal_envelope=goal))

    assert decision.status == "replan"
    assert decision.diagnosis["primary_gap"] == "grounded_evidence_missing"
    assert decision.diagnosis["goal_status"] == "insufficient_coverage"
    assert decision.strategy_guidance["recommended_strategy"] == "gather_grounded_evidence"
    assert "final_response" in decision.blocked_next_node_types


def test_judge_progress_uses_model_replan_for_missing_verification(monkeypatch):
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {
            "status": "replan",
            "reason": "Verification is still missing.",
            "diagnosis": {"primary_gap": "verification_missing"},
            "strategy_guidance": {"recommended_strategy": "verify_existing_changes"},
            "allowed_next_node_types": ["verification"],
            "blocked_next_node_types": ["final_response"],
            "must_cover": ["verify current result"],
        },
    )

    goal = GoalEnvelope(goal="修改 README.md", normalized_goal="修改 README.md", intent="change_and_verify", success_criteria=["verify result"])
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload(
            {
                "summary": "updated readme",
                "evidence_items": [{"kind": "path", "path": "README.md"}],
            }
        ),
        new_agent_graph_state(run_id="judge-3", goal_envelope=goal),
    )

    assert decision.status == "replan"
    assert decision.diagnosis["primary_gap"] == "verification_missing"
    assert decision.strategy_guidance["recommended_strategy"] == "verify_existing_changes"
    assert "verification" in decision.allowed_next_node_types
    assert "final_response" in decision.blocked_next_node_types
    assert "verify current result" in decision.must_cover


def test_judge_progress_uses_model_replan_for_ambiguity(monkeypatch):
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {
            "status": "replan",
            "reason": "The target is still ambiguous.",
            "allowed_next_node_types": ["clarification"],
            "blocked_next_node_types": ["final_response"],
        },
    )

    goal = GoalEnvelope(goal="讲解 service 模块", normalized_goal="讲解 service 模块", intent="target_explainer")
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload({"open_questions": ["Which service file should be used?"]}),
        new_agent_graph_state(run_id="judge-4", goal_envelope=goal),
    )

    assert decision.status == "replan"
    assert "clarification" in decision.allowed_next_node_types
    assert "final_response" in decision.blocked_next_node_types


def test_judge_progress_stops_due_to_cost_when_iteration_budget_is_exceeded():
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    goal = GoalEnvelope(goal="长任务", normalized_goal="长任务", intent="compound", constraints={"max_iterations": 1})
    state = new_agent_graph_state(run_id="judge-5", goal_envelope=goal)
    state.current_iteration = 1

    decision = judge_progress(goal, normalize_aggregated_workflow_payload({}), state)

    assert decision.status == "replan"
    assert "final_response" in decision.blocked_next_node_types


def test_judge_progress_uses_model_replan_for_candidate_only_progress(monkeypatch):
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {
            "status": "replan",
            "reason": "Candidate search results are not enough yet.",
            "diagnosis": {"primary_gap": "grounded_evidence_missing"},
            "allowed_next_node_types": ["content_search", "plan_read"],
        },
    )

    goal = GoalEnvelope(goal="解释 service 模块职责", normalized_goal="解释 service 模块职责", intent="compound", success_criteria=["collect evidence"])
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload(
            {
                "summaries": ["found some likely files"],
                "quality_signals": [
                    {
                        "source": "content_search",
                        "relevance": "medium",
                        "confidence": 0.6,
                        "progress_contribution": "candidate_identified",
                        "verification_needed": False,
                        "recoverable_error": False,
                    }
                ],
                "reasoning_trace": [{"kind": "search_strategy", "summary": "search only"}],
            }
        ),
        new_agent_graph_state(run_id="judge-6", goal_envelope=goal),
    )

    assert decision.status == "replan"
    assert decision.diagnosis["primary_gap"] == "grounded_evidence_missing"


def test_judge_progress_file_read_replans_when_model_requires_direct_read(monkeypatch):
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {
            "status": "replan",
            "reason": "Search hits are not enough; read the file body next.",
            "allowed_next_node_types": ["plan_read", "chunked_file_read"],
            "blocked_next_node_types": ["final_response"],
        },
    )

    goal = GoalEnvelope(goal="解释根目录 README", normalized_goal="解释根目录 README", intent="file_read", success_criteria=["read target"])
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload(
            {
                "summaries": ["found README candidate"],
                "evidence_items": [{"kind": "search_hit", "path": "README.md", "summary": "matched search terms"}],
                "quality_signals": [
                    {
                        "source": "content_search",
                        "relevance": "high",
                        "confidence": 0.8,
                        "progress_contribution": "candidate_identified",
                        "verification_needed": False,
                        "recoverable_error": False,
                    }
                ],
            }
        ),
        new_agent_graph_state(run_id="judge-read-routing", goal_envelope=goal),
    )

    assert decision.status == "replan"
    assert "plan_read" in decision.allowed_next_node_types
    assert "chunked_file_read" in decision.allowed_next_node_types
    assert "final_response" in decision.blocked_next_node_types


def test_judge_progress_detects_conflicting_evidence():
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    goal = GoalEnvelope(goal="确认主入口文件", normalized_goal="确认主入口文件", intent="file_read", success_criteria=["resolve target"])
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload(
            {
                "summaries": ["found conflicting candidates"],
                "evidence_items": [{"kind": "path", "path": "README.md"}],
                "quality_signals": [
                    {
                        "source": "content_search",
                        "relevance": "high",
                        "confidence": 0.8,
                        "progress_contribution": "candidate_identified",
                        "verification_needed": True,
                        "recoverable_error": False,
                    }
                ],
                "conflicts": ["multiple entrypoints disagree"],
            }
        ),
        new_agent_graph_state(run_id="judge-7", goal_envelope=goal),
    )

    assert decision.status == "replan"
    assert decision.diagnosis["primary_gap"] == "conflicting_evidence"
    assert decision.strategy_guidance["recommended_strategy"] == "resolve_conflict_before_answering"


def test_judge_progress_uses_memory_excluded_targets_as_conflicts():
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    goal = GoalEnvelope(goal="解释根目录 README", normalized_goal="解释根目录 README", intent="file_read")
    state = new_agent_graph_state(run_id="judge-memory-1", goal_envelope=goal)
    state.memory_state.semantic_memory = {"excluded_targets": ["frontend-shell/README.md"]}

    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload(
            {
                "summaries": ["found excluded candidate"],
                "evidence_items": [{"kind": "path", "relative_path": "frontend-shell/README.md"}],
            }
        ),
        state,
    )

    assert decision.status == "replan"
    assert decision.diagnosis["primary_gap"] == "conflicting_evidence"


def test_judge_progress_rejects_evidence_when_confirmed_target_differs():
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    goal = GoalEnvelope(goal="解释根目录 README", normalized_goal="解释根目录 README", intent="file_read")
    state = new_agent_graph_state(run_id="judge-memory-2", goal_envelope=goal)
    state.memory_state.semantic_memory = {
        "confirmed_targets": ["README.md"],
        "read_plan": {"target_path": "README.md"},
    }

    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload(
            {
                "summaries": ["read docs readme"],
                "evidence_items": [{"kind": "path", "path": "docs/README.md"}],
                "chunks": [{"path": "docs/README.md", "start_line": 1, "end_line": 3}],
            }
        ),
        state,
    )

    assert decision.status == "replan"
    assert decision.diagnosis["primary_gap"] == "conflicting_evidence"


def test_judge_progress_rejects_read_plan_path_mismatch():
    from agent_runtime_framework.workflow.planning.judge import judge_progress

    goal = GoalEnvelope(goal="解释根目录 README", normalized_goal="解释根目录 README", intent="file_read")
    state = new_agent_graph_state(run_id="judge-memory-3", goal_envelope=goal)
    state.memory_state.semantic_memory = {"read_plan": {"target_path": "README.md"}}

    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload(
            {
                "summaries": ["read docs readme"],
                "chunks": [{"path": "docs/README.md", "start_line": 1, "end_line": 3}],
            }
        ),
        state,
    )

    assert decision.status == "replan"
    assert decision.diagnosis["primary_gap"] == "conflicting_evidence"


def test_final_response_executor_rejects_execution_when_judge_has_not_accepted():
    executor = FinalResponseExecutor()
    run = WorkflowRun(goal="demo", shared_state={"judge_decision": {"status": "replan", "reason": "need more"}})

    result = executor.execute(WorkflowNode(node_id="final", node_type="final_response"), run)

    assert result.status == NODE_STATUS_FAILED
    assert "judge" in str(result.error).lower()


def test_agent_graph_runtime_limited_answer_contains_replan_reason_and_missing_items():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(goal="长任务", normalized_goal="长任务", intent="compound")
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor(), "final_response": FinalResponseExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=state.current_iteration + 1,
            planner_summary="still working",
            nodes=[PlannedNode(node_id=f"workspace_subtask_{state.current_iteration + 1}", node_type="workspace_subtask", reason="continue", success_criteria=["progress"])],
            edges=[],
            metadata={"parent_judge_id": "plan_1"},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {
            "status": "replan",
            "reason": "cost budget exhausted",
            "missing_evidence": ["verification", "more code evidence"],
        },
        max_iterations=2,
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_COMPLETED
    assert "cost budget exhausted" in result.final_output
    assert "verification" in result.final_output
    assert "more code evidence" in result.final_output


def test_agent_graph_runtime_returns_clarification_branch_when_graph_requests_it():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(goal="讲解 service 模块", normalized_goal="讲解 service 模块", intent="target_explainer")
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor(), "clarification": ClarificationExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=state.current_iteration + 1,
            planner_summary="need clarification",
            nodes=[PlannedNode(node_id="clarification_1", node_type="clarification", reason="ask follow-up", success_criteria=["request clarification"])],
            edges=[],
            metadata={"parent_judge_id": "plan_1"},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "replan", "reason": "多个可能目标，请先明确路径"},
        max_iterations=2,
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_WAITING_INPUT
    assert result.pending_interaction is not None
    assert result.pending_interaction.kind == "clarification"
    assert result.pending_interaction.prompt
    assert result.final_output is None


def test_agent_graph_runtime_confirmed_read_short_path_skips_search_and_clarification(monkeypatch):
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime
    from agent_runtime_framework.workflow.planning.subgraph_planner import plan_next_subgraph

    class PlanReadExecutor:
        def execute(self, node, run, context=None):
            return NodeResult(status=NODE_STATUS_COMPLETED, output={"summary": "read plan ready"})

    class ReadExecutor:
        def execute(self, node, run, context=None):
            return NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summary": "read readme",
                    "evidence_items": [{"kind": "file_chunk", "path": "README.md", "summary": "readme chunk"}],
                    "chunks": [{"path": "README.md", "start_line": 1, "end_line": 3, "text": "hello"}],
                    "quality_signals": [
                        {
                            "source": "chunked_file_read",
                            "relevance": "high",
                            "confidence": 0.9,
                            "progress_contribution": "grounded_evidence_collected",
                            "verification_needed": False,
                            "recoverable_error": False,
                        }
                    ],
                },
                references=["README.md"],
            )

    monkeypatch.setattr(
        "agent_runtime_framework.workflow.nodes.core.synthesize_text",
        lambda context, role, system_prompt, payload, max_tokens: "final response",
    )
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.planning.judge.chat_json",
        lambda *args, **kwargs: {
            "status": "accept",
            "reason": "Collected sufficient evidence",
            "allowed_next_node_types": ["final_response"],
        },
    )

    goal = GoalEnvelope(goal="解释根目录 README", normalized_goal="解释根目录 README", intent="file_read")
    runtime = RecordingGraphExecutionRuntime(
        executors={
            "plan_read": PlanReadExecutor(),
            "chunked_file_read": ReadExecutor(),
            "final_response": FinalResponseExecutor(),
        }
    )
    agent_runtime = AgentGraphRuntime(workflow_runtime=runtime, planner=plan_next_subgraph, max_iterations=1)
    prior_state = {
        "run_id": "confirmed-read-run",
        "goal_envelope": goal.as_payload(),
        "memory_state": {
            "clarification_memory": {},
            "semantic_memory": {
                "confirmed_targets": ["README.md"],
                "interpreted_target": {"confirmed": True, "preferred_path": "README.md"},
            },
            "execution_memory": {},
            "preference_memory": {},
        },
    }

    result = agent_runtime.run(goal, prior_state=prior_state, context={})

    assert result.status == RUN_STATUS_COMPLETED
    assert result.final_output == "final response"
    assert runtime.calls[0] == ["plan_read", "chunked_file_read"]


def test_agent_graph_runtime_records_execution_summary_for_replanning():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(
        goal="创建 tet.txt 并写入内容",
        normalized_goal="创建 tet.txt 并写入内容",
        intent="change_and_verify",
        target_hints=["tet.txt"],
        success_criteria=["write file"],
    )
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"create_path": NoopExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="create file",
            nodes=[PlannedNode(node_id="create_path_1", node_type="create_path", reason="create", success_criteria=["create file"])],
            edges=[],
            metadata={},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "accepted", "reason": "done"},
    )

    result = runtime.run(goal, context={})

    assert result.metadata["agent_graph_state"]["execution_summary"]["current_iteration"] == 1
    assert result.metadata["agent_graph_state"]["execution_summary"]["last_judge_status"] == "accepted"
    assert result.metadata["agent_graph_state"]["execution_summary"]["open_issues"] == []
    assert result.metadata["agent_graph_state"]["attempted_strategies"] == ["create file"]
    assert result.metadata["agent_graph_state"]["iteration_summaries"][0]["planner_summary"] == "create file"


def test_agent_graph_runtime_does_not_copy_append_history_into_run_metadata():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="compound")
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="collect evidence",
            nodes=[PlannedNode(node_id="workspace_subtask_1", node_type="workspace_subtask", reason="collect", success_criteria=["progress"])],
            edges=[],
            metadata={"parent_judge_id": "plan_1"},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "accepted", "reason": "done"},
    )

    result = runtime.run(goal)

    assert "append_history" not in result.metadata
    assert result.graph.metadata["append_history"][0]["parent_judge_id"] == "plan_1"


def test_agent_graph_runtime_exposes_repair_history_in_graph_state_and_execution_summary():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(goal="创建 tet.txt 并写入内容", normalized_goal="创建 tet.txt 并写入内容", intent="change_and_verify")
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="create file",
            nodes=[PlannedNode(node_id="workspace_subtask_1", node_type="workspace_subtask", reason="create", success_criteria=["progress"])],
            edges=[],
            metadata={},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "accepted", "reason": "done"},
    )

    result = runtime.run(goal, context={}, prior_state={"run_id": "repair-run-1", "goal_envelope": goal.as_payload(), "repair_history": [{
        "contract_kind": "read_plan",
        "role": "planner",
        "success": True,
        "attempts_used": 2,
        "max_attempts": 3,
        "initial_error": "missing preferred_regions",
    }]})

    assert result.metadata["agent_graph_state"]["repair_history"][0]["contract_kind"] == "read_plan"
    assert result.metadata["agent_graph_state"]["execution_summary"]["repair_count"] == 1
    assert result.metadata["agent_graph_state"]["execution_summary"]["latest_repair"]["attempts_used"] == 2


def test_agent_graph_runtime_tracks_failure_history_and_open_issues():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(
        goal="总结 docs",
        normalized_goal="总结 docs",
        intent="compound",
        success_criteria=["collect evidence"],
    )
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="collect baseline evidence",
            nodes=[PlannedNode(node_id="workspace_subtask_1", node_type="workspace_subtask", reason="collect", success_criteria=["progress"])],
            edges=[],
            metadata={},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {
            "status": "replan",
            "reason": "still missing grounded evidence",
            "missing_evidence": ["grounded evidence"],
            "diagnosis": {"primary_gap": "grounded_evidence_missing", "goal_status": "insufficient_coverage"},
            "strategy_guidance": {"recommended_strategy": "gather_grounded_evidence"},
        },
        max_iterations=1,
    )

    result = runtime.run(goal, context={})

    assert result.metadata["agent_graph_state"]["open_issues"] == ["grounded evidence"]
    assert result.metadata["agent_graph_state"]["failure_history"][0]["status"] == "replan"
    assert result.metadata["agent_graph_state"]["failure_history"][0]["diagnosis"]["primary_gap"] == "grounded_evidence_missing"
    assert result.metadata["agent_graph_state"]["execution_summary"]["latest_failure"]["status"] == "replan"
    assert result.metadata["agent_graph_state"]["recovery_history"][0]["action"] == "replan"
    assert result.metadata["agent_graph_state"]["execution_summary"]["latest_recovery_decision"]["action"] == "replan"


def test_agent_graph_runtime_records_replan_recovery_decision_for_clarification_route():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(goal="讲解 service 模块", normalized_goal="讲解 service 模块", intent="target_explainer")
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor(), "clarification": ClarificationExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="probe target",
            nodes=[PlannedNode(node_id="clarification_1", node_type="clarification", reason="ask user", success_criteria=["request clarification"])],
            edges=[],
            metadata={},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {
            "status": "replan",
            "reason": "多个可能目标，请先明确路径",
            "missing_evidence": ["target path"],
            "diagnosis": {"primary_gap": "clarification_missing"},
            "strategy_guidance": {"recommended_strategy": "request_target_clarification"},
        },
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_WAITING_INPUT
    assert result.metadata["agent_graph_state"]["recovery_history"][0]["action"] == "replan"
    assert result.metadata["agent_graph_state"]["recovery_history"][0]["trigger"] == "replan"


def test_agent_graph_runtime_records_execution_failure_recovery_decision():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="compound")
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": FailExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="collect evidence",
            nodes=[PlannedNode(node_id="workspace_subtask_1", node_type="workspace_subtask", reason="collect", success_criteria=["progress"])],
            edges=[],
            metadata={},
        ),
        max_iterations=1,
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_FAILED
    assert result.metadata["agent_graph_state"]["recovery_history"][0]["action"] == "diagnose_and_replan"
    assert result.metadata["agent_graph_state"]["recovery_history"][0]["trigger"] == "execution_failed"


def test_system_node_manager_keeps_new_verification_over_stale_evidence_synthesis():
    from agent_runtime_framework.workflow.orchestration.system_nodes import SystemNodeManager

    manager = SystemNodeManager()
    run = WorkflowRun(
        goal="创建 tet.txt 并写入内容",
        graph=WorkflowGraph(nodes=[WorkflowNode(node_id="verification_2", node_type="verification")], edges=[]),
        shared_state={"evidence_synthesis": {"summary": "", "verification": None, "verification_events": []}},
    )
    executed = WorkflowRun(goal="创建 tet.txt 并写入内容")
    executed.shared_state["node_results"] = {
        "verification_2": NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": "verified",
                "verification": {"status": "passed", "success": True, "summary": "verified"},
                "verification_events": [{"status": "passed", "success": True, "summary": "verified", "verification_type": "post_write"}],
            },
        )
    }
    subgraph = PlannedSubgraph(
        iteration=2,
        planner_summary="verify",
        nodes=[PlannedNode(node_id="verification_2", node_type="verification", reason="verify", success_criteria=["verify"])],
        edges=[],
        metadata={},
    )

    aggregated_result, evidence_result = manager.materialize_iteration_system_nodes(run, executed, subgraph)

    assert aggregated_result.output["verification"]["status"] == "passed"
    assert evidence_result.output["verification"]["status"] == "passed"


def test_agent_graph_state_store_restores_workflow_run_with_resume_token():
    from agent_runtime_framework.workflow.state.graph_state_store import AgentGraphStateStore

    store = AgentGraphStateStore()
    run = store.restore_workflow_run(
        {
            "run_id": "run-1",
            "goal": "demo",
            "status": "waiting_approval",
            "graph": {
                "nodes": [{"node_id": "change", "node_type": "workspace_subtask"}],
                "edges": [],
                "metadata": {},
            },
            "shared_state": {
                "resume_token": {
                    "token_id": "token-1",
                    "node_id": "change",
                }
            },
            "node_states": {
                "change": {
                    "node_id": "change",
                    "status": "waiting_approval",
                    "result": {
                        "status": "waiting_approval",
                        "output": {"summary": "needs approval"},
                        "approval_data": {"kind": "workspace_subtask"},
                    },
                }
            },
        }
    )

    assert run.run_id == "run-1"
    assert run.shared_state["resume_token"].token_id == "token-1"
    assert run.node_states["change"].result.approval_data["kind"] == "workspace_subtask"


def test_system_node_manager_seeds_goal_context_and_plan_nodes():
    from agent_runtime_framework.workflow.orchestration.system_nodes import SystemNodeManager

    manager = SystemNodeManager()
    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
    )
    run = WorkflowRun(
        goal=goal.goal,
        graph=WorkflowGraph(
            nodes=[
                WorkflowNode(node_id="goal_intake", node_type="goal_intake"),
                WorkflowNode(node_id="context_assembly", node_type="context_assembly", dependencies=["goal_intake"]),
                WorkflowNode(node_id="plan_1", node_type="plan", dependencies=["context_assembly"]),
            ],
            edges=[],
        ),
    )

    manager.seed_system_nodes(
        run,
        goal,
        {"memory": {"summary": "memo"}, "policy_context": {"mode": "workspace_write"}, "workspace_root": "/tmp/demo"},
    )

    assert run.node_states["goal_intake"].result.output["goal"] == "读取 README.md"
    assert run.node_states["context_assembly"].result.output["workspace_root"] == "/tmp/demo"
    assert run.node_states["plan_1"].result.output["summary"] == "prepared plan_1"


def test_system_node_manager_materializes_iteration_nodes():
    from agent_runtime_framework.workflow.orchestration.system_nodes import SystemNodeManager

    manager = SystemNodeManager()
    run = WorkflowRun(
        goal="读取 README.md",
        graph=WorkflowGraph(
            nodes=[WorkflowNode(node_id="content_search_1", node_type="content_search")],
            edges=[],
        ),
    )
    executed = WorkflowRun(goal="读取 README.md")
    executed.shared_state["node_results"] = {
        "content_search_1": NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": "found README",
                "facts": [{"kind": "file", "path": "README.md"}],
                "evidence_items": [{"kind": "path", "path": "README.md", "summary": "README.md"}],
            },
            references=["README.md"],
        )
    }
    subgraph = PlannedSubgraph(
        iteration=1,
        planner_summary="read file",
        nodes=[PlannedNode(node_id="content_search_1", node_type="content_search", reason="locate", success_criteria=["find file"])],
        edges=[],
        metadata={},
    )

    aggregated_result, evidence_result = manager.materialize_iteration_system_nodes(run, executed, subgraph)

    assert aggregated_result.output["facts"][0]["path"] == "README.md"
    assert evidence_result.output["evidence_items"][0]["path"] == "README.md"
    assert "aggregate_results_1" in run.node_states
    assert "evidence_synthesis_1" in run.node_states


def test_agent_graph_runtime_routes_clarification_through_graph_execution_runtime():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(goal="讲解 service 模块", normalized_goal="讲解 service 模块", intent="target_explainer")
    runtime = AgentGraphRuntime(
        workflow_runtime=NoDirectExecuteRuntime(
            executors={
                "clarification": ClarificationExecutor(),
            }
        ),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="probe",
            nodes=[PlannedNode(node_id="clarification_1", node_type="clarification", reason="ask user", success_criteria=["request clarification"])],
            edges=[],
            metadata={},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "replan", "reason": "多个可能目标，请先明确路径"},
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_WAITING_INPUT
    assert runtime.workflow_runtime.calls[-1] == ["clarification"]
    assert result.pending_interaction is not None
    assert result.pending_interaction.kind == "clarification"


def test_routing_runtime_does_not_rewrite_clarification_reply_into_target_explainer():
    captured = {}
    from agent_runtime_framework.api.services.chat_service import ChatService

    runtime_state = SimpleNamespace(
        ensure_session=lambda: None,
        workflow_runtime_context=lambda: {},
        _pending_workflow_interaction={"run_id": "run-1", "kind": "clarification"},
        _last_route_decision=None,
        context=SimpleNamespace(application_context=None),
        workspace=".",
        _workflow_store=SimpleNamespace(save=lambda run: None),
        record_run=lambda payload, message: None,
    )
    service = ChatService(runtime_state, SimpleNamespace(), SimpleNamespace(error_payload=lambda exc: {"status": "error"}))

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(
        "agent_runtime_framework.api.services.chat_service.analyze_goal",
        lambda message, context: SimpleNamespace(
            original_goal=message,
            primary_intent="file_read",
            requires_target_interpretation=False,
            requires_search=False,
            requires_read=True,
            requires_verification=False,
            metadata={},
        ),
    )
    monkeypatch.setattr(
        ChatService,
        "_run_agent_branch",
        lambda self, message, goal_spec, root_graph, process_sink=None: (captured.__setitem__("goal", goal_spec) or {"status": "completed"}),
    )
    try:
        service._run_root_graph("需要的是README.md这个文档")
    finally:
        monkeypatch.undo()

    assert captured["goal"].primary_intent == "file_read"
    assert captured["goal"].requires_read is True


def test_agent_graph_runtime_fails_when_planner_emits_unsupported_node_type():
    from agent_runtime_framework.workflow.runtime.agent_graph import AgentGraphRuntime

    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="compound")
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"final_response": FinalResponseExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="bad plan",
            nodes=[PlannedNode(node_id="bad_1", node_type="unsupported_node", reason="bad", success_criteria=["none"])],
            edges=[],
            metadata={},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "accepted", "reason": "done"},
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_FAILED
    assert "unsupported_node" in str(result.error or result.node_states["bad_1"].error)
