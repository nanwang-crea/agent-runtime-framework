from agent_runtime_framework.workflow import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_FAILED,
    GoalEnvelope,
    PlannedNode,
    PlannedSubgraph,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_WAITING_APPROVAL,
    NodeResult,
    NodeState,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    new_agent_graph_state,
    normalize_aggregated_workflow_payload,
)
import pytest
from agent_runtime_framework.workflow.execution_runtime import GraphExecutionRuntime
from agent_runtime_framework.workflow.scheduler import WorkflowScheduler
from agent_runtime_framework.workflow.tool_call_executor import ToolCallExecutor
from agent_runtime_framework.workflow.clarification_executor import ClarificationExecutor
from agent_runtime_framework.workflow.aggregator import aggregate_node_results
from agent_runtime_framework.workflow.target_resolution_executor import TargetResolutionExecutor
from agent_runtime_framework.workflow.node_executors import FinalResponseExecutor, VerificationExecutor
from agent_runtime_framework.workflow.discovery_executor import WorkspaceDiscoveryExecutor
from agent_runtime_framework.workflow.content_search_executor import ContentSearchExecutor
from agent_runtime_framework.workflow.chunked_file_read_executor import ChunkedFileReadExecutor
from agent_runtime_framework.workflow.evidence_synthesis_executor import EvidenceSynthesisExecutor
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.core.specs import ToolSpec
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
        run.status = RUN_STATUS_COMPLETED
        return run

    def resume(self, run, *, resume_token, approved):
        raise AssertionError("resume should not be used in this test")

    def _execute(self, executor, node, run):
        raise AssertionError("AgentGraphRuntime should not call _execute directly")


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



def test_runtime_executes_tool_call_node_with_registered_tool():
    def _echo_tool(_task, _context, arguments):
        return {"summary": f"echo:{arguments['text']}"}

    tool_registry = ToolRegistry([
        ToolSpec(
            name="echo_tool",
            description="Echo input",
            executor=_echo_tool,
            required_arguments=("text",),
        )
    ])
    app_context = SimpleNamespace(tools=tool_registry, services={})
    workflow_context = SimpleNamespace(application_context=app_context, services={})
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="call", node_type="tool_call", metadata={"tool_name": "echo_tool", "arguments": {"text": "hello"}}),
            WorkflowNode(node_id="finish", node_type="final_response", dependencies=["call"]),
        ],
        edges=[WorkflowEdge(source="call", target="finish")],
    )
    run = WorkflowRun(goal="echo", graph=graph)

    result = GraphExecutionRuntime(
        executors={"tool_call": ToolCallExecutor(), "final_response": FinalResponseExecutor()},
        context={"application_context": app_context, "workspace_context": workflow_context},
    ).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["call"].result.output["tool_name"] == "echo_tool"
    assert result.node_states["call"].result.output["summary"] == "echo:hello"
    assert result.final_output == "echo:hello"


def test_runtime_executes_clarification_node_inside_workflow():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="clarify", node_type="clarification", metadata={"prompt": "Please clarify the target file."}),
            WorkflowNode(node_id="finish", node_type="final_response", dependencies=["clarify"]),
        ],
        edges=[WorkflowEdge(source="clarify", target="finish")],
    )
    run = WorkflowRun(goal="clarify", graph=graph)

    result = GraphExecutionRuntime(
        executors={"clarification": ClarificationExecutor(), "final_response": FinalResponseExecutor()}
    ).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["clarify"].result.output["clarification_required"] is True
    assert result.shared_state["clarification_request"]["prompt"] == "Please clarify the target file."
    assert "Please clarify the target file." in result.final_output



def test_runtime_executes_target_explainer_node_chain_with_workspace_tools(tmp_path):
    package = tmp_path / "src"
    package.mkdir()
    target = package / "service.py"
    target.write_text("def run():\n    return 'ok'\n", encoding="utf-8")

    from agent_runtime_framework.agents.workspace_backend import build_default_workspace_tools, WorkspaceContext
    from agent_runtime_framework.applications import ApplicationContext

    app_context = ApplicationContext(resource_repository=LocalFileResourceRepository([tmp_path]), config={"default_directory": str(tmp_path)})
    for tool in build_default_workspace_tools():
        app_context.tools.register(tool)
    workspace_context = WorkspaceContext(application_context=app_context)
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="resolve", node_type="target_resolution", metadata={"query": "请讲解 service 这个模块在做什么"}),
            WorkflowNode(node_id="search", node_type="content_search", dependencies=["resolve"]),
            WorkflowNode(node_id="read", node_type="chunked_file_read", dependencies=["search"]),
            WorkflowNode(node_id="synthesize", node_type="evidence_synthesis", dependencies=["read"]),
            WorkflowNode(node_id="finish", node_type="final_response", dependencies=["synthesize"]),
        ],
        edges=[
            WorkflowEdge(source="resolve", target="search"),
            WorkflowEdge(source="search", target="read"),
            WorkflowEdge(source="read", target="synthesize"),
            WorkflowEdge(source="synthesize", target="finish"),
        ],
    )
    run = WorkflowRun(goal="请讲解 service 这个模块在做什么", graph=graph)

    result = GraphExecutionRuntime(
        executors={
            "target_resolution": TargetResolutionExecutor(),
            "content_search": ContentSearchExecutor(),
            "chunked_file_read": ChunkedFileReadExecutor(),
            "evidence_synthesis": EvidenceSynthesisExecutor(),
            "final_response": FinalResponseExecutor(),
        },
        context={"application_context": app_context, "workspace_context": workspace_context, "workspace_root": str(tmp_path)},
    ).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["resolve"].result.output["resolution_status"] == "resolved"
    assert result.node_states["search"].result.output["candidates"][0]["relative_path"] == "src/service.py"
    assert result.node_states["read"].result.output["path"] == "src/service.py"
    assert "src/service.py" in result.final_output


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
    }
    assert aggregated.references == ["README.md", "src"]


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


def test_verification_executor_returns_not_run_when_no_verification_event_exists():
    run = WorkflowRun(
        goal="verify",
        shared_state={
            "node_results": {
                "aggregate_results": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={"summary": "aggregate", "verification_events": []},
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
        "status": "not_run",
        "success": False,
        "summary": "No explicit verification result was produced.",
    }


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


def test_workspace_discovery_executor_emits_candidates_facts_and_evidence(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    package = tmp_path / "agent_runtime_framework"
    package.mkdir()
    (package / "app.py").write_text("print('ok')\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")

    result = WorkspaceDiscoveryExecutor().execute(
        WorkflowNode(node_id="discover", node_type="workspace_discovery"),
        WorkflowRun(goal="解释一下这个仓库的结构"),
        context={"workspace_root": str(tmp_path)},
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["artifacts"]["tree_sample"]
    assert any(item["kind"] == "directory" and item["path"].endswith("agent_runtime_framework") for item in result.output["evidence_items"])
    assert any(fact["kind"] == "source_root" for fact in result.output["facts"])
    assert any(fact["kind"] == "test_root" for fact in result.output["facts"])
    assert any(fact["kind"] == "config_or_entry" and fact["path"].endswith("README.md") for fact in result.output["facts"])


def test_content_search_executor_ranks_targets_and_emits_search_hit_evidence(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    readme = tmp_path / "README.md"
    readme.write_text("workspace overview and setup\n", encoding="utf-8")
    service_file = docs_dir / "service.md"
    service_file.write_text("service module design\nservice runtime details\n", encoding="utf-8")

    run = WorkflowRun(
        goal="请解释 service 模块",
        shared_state={
            "node_results": {
                "discover": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "summary": "discovered",
                        "evidence_items": [
                            {"kind": "path", "path": str(readme), "summary": "README"},
                            {"kind": "path", "path": str(service_file), "summary": "Service doc"},
                        ],
                    },
                    references=[str(readme), str(service_file)],
                )
            }
        },
    )

    result = ContentSearchExecutor().execute(
        WorkflowNode(node_id="search", node_type="content_search"),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["ranked_targets"][0]["path"].endswith("service.md")
    assert any(item["kind"] == "search_hit" and item["path"].endswith("service.md") for item in result.output["evidence_items"])
    assert any(match["path"].endswith("service.md") for match in result.output["matches"])


def test_chunked_file_read_executor_reads_small_file_as_single_chunk(tmp_path):
    target = tmp_path / "README.md"
    target.write_text("line1\nline2\n", encoding="utf-8")

    result = ChunkedFileReadExecutor().execute(
        WorkflowNode(node_id="read", node_type="chunked_file_read", metadata={"target_path": "README.md"}),
        WorkflowRun(goal="读取 README"),
        context={"workspace_root": str(tmp_path)},
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert len(result.output["chunks"]) == 1
    assert result.output["chunks"][0]["start_line"] == 1
    assert result.output["chunks"][0]["end_line"] == 2
    assert result.output["chunks"][0]["text"] == "line1\nline2"
    assert result.output["evidence_items"][0]["kind"] == "file_chunk"


def test_chunked_file_read_executor_reads_window_around_search_hit_for_large_file(tmp_path):
    target = tmp_path / "service.py"
    target.write_text("\n".join(f"line {index}" for index in range(1, 51)), encoding="utf-8")

    run = WorkflowRun(
        goal="解释 service.py",
        shared_state={
            "node_results": {
                "search": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "ranked_targets": [{"path": str(target), "score": 5}],
                        "matches": [{"path": str(target), "line": 25, "score": 5}],
                    },
                    references=[str(target)],
                )
            }
        },
    )

    result = ChunkedFileReadExecutor(max_chars=80, window_radius=2).execute(
        WorkflowNode(node_id="read", node_type="chunked_file_read"),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert len(result.output["chunks"]) == 1
    assert result.output["chunks"][0]["start_line"] == 23
    assert result.output["chunks"][0]["end_line"] == 27
    assert "line 25" in result.output["chunks"][0]["text"]
    assert all("line 1\nline 2" not in chunk["text"] for chunk in result.output["chunks"])


def test_evidence_synthesis_executor_builds_summary_facts_and_open_questions():
    run = WorkflowRun(
        goal="解释当前仓库结构",
        shared_state={
            "aggregated_result": NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summaries": ["发现了源码目录和测试目录"],
                    "facts": [{"kind": "source_root", "path": "src"}],
                    "evidence_items": [{"kind": "directory", "path": "src", "summary": "Source root"}],
                    "open_questions": ["missing config file"],
                    "artifacts": {"tree_sample": ["src/"]},
                },
                references=["src"],
            )
        },
    )

    result = EvidenceSynthesisExecutor().execute(
        WorkflowNode(node_id="synthesize", node_type="evidence_synthesis"),
        run,
    )

    assert result.status == NODE_STATUS_COMPLETED
    assert result.output["summary"]
    assert result.output["facts"] == [{"kind": "source_root", "path": "src"}]
    assert result.output["open_questions"] == ["missing config file"]
    assert run.shared_state["evidence_synthesis"]["summary"] == result.output["summary"]
    assert "response_synthesis" not in run.shared_state


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


def test_final_response_executor_falls_back_to_evidence_rich_aggregate_payload():
    run = WorkflowRun(
        goal="解释仓库",
        shared_state={
            "aggregated_result": NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summaries": [],
                    "facts": [{"kind": "source_root", "path": "src"}],
                    "evidence_items": [{"kind": "directory", "path": "src", "summary": "Source root"}],
                    "verification": {"status": "not_run", "success": False, "summary": "No explicit verification result was produced."},
                },
                references=["src"],
            )
        },
    )

    result = FinalResponseExecutor().execute(
        WorkflowNode(node_id="final_response", node_type="final_response"),
        run,
    )

    assert "src" in result.output["final_response"]
    assert "not_run" in result.output["final_response"] or "未验证" in result.output["final_response"] or "No explicit verification" in result.output["final_response"]



def test_content_search_executor_prefers_symbol_hint_and_source_extensions(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    service_py = src_dir / "billing.py"
    service_py.write_text("class BillingService\n    def run(self):\n        return 'ok'\n".replace("\n    def", ":\n    def"), encoding="utf-8")
    service_md = docs_dir / "billing.md"
    service_md.write_text("BillingService overview\n", encoding="utf-8")

    run = WorkflowRun(
        goal="解释 BillingService",
        shared_state={
            "node_results": {
                "discover": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "evidence_items": [
                            {"kind": "path", "path": str(service_py), "summary": "python source"},
                            {"kind": "path", "path": str(service_md), "summary": "docs"},
                        ],
                    },
                    references=[str(service_py), str(service_md)],
                )
            }
        },
    )

    result = ContentSearchExecutor().execute(
        WorkflowNode(node_id="search", node_type="content_search", metadata={"symbol_hint": "BillingService"}),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    assert result.output["ranked_targets"][0]["relative_path"] == "src/billing.py"
    assert "billingservice" in result.output["ranked_targets"][0]["matched_terms"]


def test_content_search_executor_returns_line_hits_with_context(tmp_path):
    target = tmp_path / "service.py"
    target.write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "class BillingService:",
                "    def run(self):",
                "        return 'ok'",
            ]
        ),
        encoding="utf-8",
    )

    run = WorkflowRun(
        goal="解释 BillingService run",
        shared_state={
            "node_results": {
                "discover": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={"evidence_items": [{"kind": "path", "path": str(target), "summary": "service source"}]},
                    references=[str(target)],
                )
            }
        },
    )

    result = ContentSearchExecutor().execute(
        WorkflowNode(node_id="search", node_type="content_search", metadata={"symbol_hint": "BillingService"}),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    top_match = result.output["matches"][0]
    assert top_match["line"] == 4
    assert "BillingService" in top_match["context"]
    assert "def run" in top_match["context"]



def test_chunked_file_read_executor_merges_multiple_hit_windows(tmp_path):
    target = tmp_path / "service.py"
    target.write_text("\n".join(f"line {index}" for index in range(1, 81)), encoding="utf-8")

    run = WorkflowRun(
        goal="解释多个命中",
        shared_state={
            "node_results": {
                "search": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "matches": [
                            {"path": str(target), "line": 10, "score": 5},
                            {"path": str(target), "line": 60, "score": 4},
                        ],
                        "ranked_targets": [{"path": str(target), "score": 5}],
                    },
                    references=[str(target)],
                )
            }
        },
    )

    result = ChunkedFileReadExecutor(max_chars=120, window_radius=2).execute(
        WorkflowNode(node_id="read", node_type="chunked_file_read"),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    assert len(result.output["chunks"]) == 2
    assert result.output["chunks"][0]["start_line"] == 8
    assert result.output["chunks"][1]["start_line"] == 58



def test_chunked_file_read_executor_merges_overlapping_hit_windows(tmp_path):
    target = tmp_path / "service.py"
    target.write_text("\n".join(f"line {index}" for index in range(1, 41)), encoding="utf-8")

    run = WorkflowRun(
        goal="解释相邻命中",
        shared_state={
            "node_results": {
                "search": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "matches": [
                            {"path": str(target), "line": 10, "score": 5},
                            {"path": str(target), "line": 12, "score": 4},
                        ],
                        "ranked_targets": [{"path": str(target), "score": 5}],
                    },
                    references=[str(target)],
                )
            }
        },
    )

    result = ChunkedFileReadExecutor(max_chars=120, window_radius=3).execute(
        WorkflowNode(node_id="read", node_type="chunked_file_read"),
        run,
        context={"workspace_root": str(tmp_path)},
    )

    assert len(result.output["chunks"]) == 1
    assert result.output["chunks"][0]["start_line"] == 7
    assert result.output["chunks"][0]["end_line"] == 15



def test_chunked_file_read_executor_reports_pagination_for_large_files(tmp_path):
    target = tmp_path / "large.txt"
    target.write_text("\n".join(f"line {index}" for index in range(1, 401)), encoding="utf-8")

    result = ChunkedFileReadExecutor(max_chars=80, window_radius=2).execute(
        WorkflowNode(node_id="read", node_type="chunked_file_read", metadata={"target_path": "large.txt"}),
        WorkflowRun(goal="读取 large.txt"),
        context={"workspace_root": str(tmp_path)},
    )

    assert result.output["artifacts"]["page"] == 1
    assert result.output["artifacts"]["has_more"] is True



def test_verification_executor_groups_events_by_verification_type():
    run = WorkflowRun(
        goal="verify",
        shared_state={
            "node_results": {
                "search": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "verification_events": [
                            {"status": "passed", "success": True, "summary": "evidence checked", "verification_type": "evidence"},
                            {"status": "passed", "success": True, "summary": "tool call succeeded", "verification_type": "tool"},
                        ]
                    },
                    references=["README.md"],
                ),
                "tests": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "verification_events": [
                            {"status": "passed", "success": True, "summary": "pytest passed", "verification_type": "test"},
                            {"status": "not_run", "success": False, "summary": "approval not requested", "verification_type": "approval"},
                        ]
                    },
                    references=[],
                ),
            }
        },
    )

    result = VerificationExecutor().execute(
        WorkflowNode(node_id="verification", node_type="verification"),
        run,
    )

    assert result.output["verification"]["status"] == "failed"
    assert result.output["verification_by_type"]["evidence"]["status"] == "passed"
    assert result.output["verification_by_type"]["tool"]["status"] == "passed"
    assert result.output["verification_by_type"]["test"]["status"] == "passed"
    assert result.output["verification_by_type"]["approval"]["status"] == "not_run"


def test_append_subgraph_appends_nodes_in_order_after_anchor():
    from agent_runtime_framework.workflow.graph_mutation import append_subgraph

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
    from agent_runtime_framework.workflow.graph_mutation import append_subgraph

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
    from agent_runtime_framework.workflow.graph_mutation import append_subgraph

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
    from agent_runtime_framework.workflow.graph_mutation import append_subgraph

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
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

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
    node_ids = [node.node_id for node in result.graph.nodes]
    assert node_ids[:3] == ["goal_intake", "context_assembly", "plan_1"]
    assert "aggregate_results_1" in node_ids
    assert "evidence_synthesis_1" in node_ids
    assert "judge_1" in node_ids


def test_agent_graph_runtime_appends_second_iteration_when_more_evidence_is_needed():
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

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
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

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


def test_judge_progress_accepts_when_evidence_and_verification_are_sufficient():
    from agent_runtime_framework.workflow.judge import judge_progress

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


def test_judge_progress_requests_more_evidence_when_payload_is_thin():
    from agent_runtime_framework.workflow.judge import judge_progress

    goal = GoalEnvelope(goal="总结 docs", normalized_goal="总结 docs", intent="compound", success_criteria=["collect evidence"])
    decision = judge_progress(goal, normalize_aggregated_workflow_payload({}), new_agent_graph_state(run_id="judge-2", goal_envelope=goal))

    assert decision.status == "needs_more_evidence"


def test_judge_progress_requests_verification_when_evidence_exists_but_verification_is_missing():
    from agent_runtime_framework.workflow.judge import judge_progress

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

    assert decision.status == "needs_verification"


def test_judge_progress_requests_clarification_when_ambiguity_is_present():
    from agent_runtime_framework.workflow.judge import judge_progress

    goal = GoalEnvelope(goal="讲解 service 模块", normalized_goal="讲解 service 模块", intent="target_explainer")
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload({"open_questions": ["Which service file should be used?"]}),
        new_agent_graph_state(run_id="judge-4", goal_envelope=goal),
    )

    assert decision.status == "needs_clarification"


def test_judge_progress_stops_due_to_cost_when_iteration_budget_is_exceeded():
    from agent_runtime_framework.workflow.judge import judge_progress

    goal = GoalEnvelope(goal="长任务", normalized_goal="长任务", intent="compound", constraints={"max_iterations": 1})
    state = new_agent_graph_state(run_id="judge-5", goal_envelope=goal)
    state.current_iteration = 1

    decision = judge_progress(goal, normalize_aggregated_workflow_payload({}), state)

    assert decision.status == "stop_due_to_cost"


def test_final_response_executor_rejects_execution_when_judge_has_not_accepted():
    executor = FinalResponseExecutor()
    run = WorkflowRun(goal="demo", shared_state={"judge_decision": {"status": "needs_more_evidence", "reason": "need more"}})

    result = executor.execute(WorkflowNode(node_id="final", node_type="final_response"), run)

    assert result.status == NODE_STATUS_FAILED
    assert "judge" in str(result.error).lower()


def test_agent_graph_runtime_limited_answer_contains_stop_reason_and_missing_items():
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

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
            "status": "stop_due_to_cost" if state.current_iteration >= 1 else "needs_more_evidence",
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


def test_agent_graph_runtime_returns_clarification_branch_when_judge_requests_it():
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

    goal = GoalEnvelope(goal="讲解 service 模块", normalized_goal="讲解 service 模块", intent="target_explainer")
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(executors={"workspace_subtask": NoopExecutor(), "clarification": ClarificationExecutor()}),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=state.current_iteration + 1,
            planner_summary="need clarification",
            nodes=[PlannedNode(node_id="workspace_subtask_1", node_type="workspace_subtask", reason="probe", success_criteria=["progress"])],
            edges=[],
            metadata={"parent_judge_id": "plan_1"},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "needs_clarification", "reason": "多个可能目标，请先明确路径"},
        max_iterations=2,
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.shared_state["clarification_request"]["clarification_required"] is True
    assert "多个可能目标" in result.final_output


def test_agent_graph_runtime_survives_model_planner_failure(monkeypatch):
    from agent_runtime_framework.workflow import subgraph_planner
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

    monkeypatch.setattr(
        subgraph_planner,
        "_plan_next_subgraph_with_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("planner offline")),
    )

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        success_criteria=["read file"],
    )
    runtime = AgentGraphRuntime(
        workflow_runtime=GraphExecutionRuntime(
            executors={
                "content_search": NoopExecutor(),
                "chunked_file_read": NoopExecutor(),
                "evidence_synthesis": NoopExecutor(),
            }
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "accepted", "reason": "done"},
    )

    result = runtime.run(goal, context={})

    assert result.status == RUN_STATUS_COMPLETED
    assert result.metadata["agent_graph_state"]
    assert result.metadata["agent_graph_state"]["planned_subgraphs"][0]["metadata"]["planner"] == "deterministic_v2"
    assert result.metadata["agent_graph_state"]["planned_subgraphs"][0]["metadata"]["fallback_reason"] == "planner offline"


def test_agent_graph_state_store_restores_workflow_run_with_resume_token():
    from agent_runtime_framework.workflow.agent_graph_state_store import AgentGraphStateStore

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
    from agent_runtime_framework.workflow.system_node_manager import SystemNodeManager

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
    from agent_runtime_framework.workflow.system_node_manager import SystemNodeManager

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


def test_agent_graph_runtime_routes_final_response_through_graph_execution_runtime():
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

    goal = GoalEnvelope(goal="读取 README.md", normalized_goal="读取 README.md", intent="file_read")
    runtime = AgentGraphRuntime(
        workflow_runtime=NoDirectExecuteRuntime(
            executors={
                "workspace_subtask": NoopExecutor(),
                "final_response": FinalResponseExecutor(),
            }
        ),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="collect",
            nodes=[PlannedNode(node_id="workspace_subtask_1", node_type="workspace_subtask", reason="collect", success_criteria=["progress"])],
            edges=[],
            metadata={},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "accepted", "reason": "done"},
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_COMPLETED
    assert "final_response" in runtime.workflow_runtime.calls[-1]


def test_agent_graph_runtime_routes_clarification_through_graph_execution_runtime():
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

    goal = GoalEnvelope(goal="讲解 service 模块", normalized_goal="讲解 service 模块", intent="target_explainer")
    runtime = AgentGraphRuntime(
        workflow_runtime=NoDirectExecuteRuntime(
            executors={
                "workspace_subtask": NoopExecutor(),
                "clarification": ClarificationExecutor(),
            }
        ),
        planner=lambda goal_envelope, state, context: PlannedSubgraph(
            iteration=1,
            planner_summary="probe",
            nodes=[PlannedNode(node_id="workspace_subtask_1", node_type="workspace_subtask", reason="collect", success_criteria=["progress"])],
            edges=[],
            metadata={},
        ),
        judge=lambda goal_envelope, aggregated_payload, state: {"status": "needs_clarification", "reason": "多个可能目标，请先明确路径"},
    )

    result = runtime.run(goal)

    assert result.status == RUN_STATUS_COMPLETED
    assert runtime.workflow_runtime.calls[-1] == ["clarification"]
    assert result.shared_state["clarification_request"]["clarification_required"] is True


def test_agent_graph_runtime_fails_when_planner_emits_unsupported_node_type():
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

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
