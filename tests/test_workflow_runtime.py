from agent_runtime_framework.workflow import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_FAILED,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_WAITING_APPROVAL,
    NodeResult,
    NodeState,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)
from agent_runtime_framework.workflow.runtime import WorkflowRuntime
from agent_runtime_framework.workflow.scheduler import WorkflowScheduler
from agent_runtime_framework.workflow.tool_call_executor import ToolCallExecutor
from agent_runtime_framework.workflow.clarification_executor import ClarificationExecutor
from agent_runtime_framework.workflow.aggregator import aggregate_node_results
from agent_runtime_framework.workflow.target_resolution_executor import TargetResolutionExecutor
from agent_runtime_framework.workflow.file_inspection_executor import FileInspectionExecutor
from agent_runtime_framework.workflow.response_synthesis_executor import ResponseSynthesisExecutor
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
    def execute(self, node, run):
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"node": node.node_id})


class FailExecutor:
    def execute(self, node, run):
        return NodeResult(status=NODE_STATUS_FAILED, error="boom")


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

    result = WorkflowRuntime(executors={"noop": NoopExecutor()}).run(run)

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

    result = WorkflowRuntime(
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
    runtime = WorkflowRuntime(executors={"approval_executor": ApprovalExecutor(), "noop": NoopExecutor()})

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

    result = WorkflowRuntime(executors={"noop": NoopExecutor()}).run(run)

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

    result = WorkflowRuntime(
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

    result = WorkflowRuntime(
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

    result = WorkflowRuntime(
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
    assert run.shared_state["response_synthesis"]["summary"] == result.output["summary"]


def test_final_response_executor_prefers_evidence_synthesis_output():
    run = WorkflowRun(
        goal="解释仓库",
        shared_state={
            "aggregated_result": NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={"summaries": ["aggregate summary"]},
                references=["README.md"],
            ),
            "response_synthesis": {"summary": "evidence summary", "facts": [{"kind": "source_root", "path": "src"}]},
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
