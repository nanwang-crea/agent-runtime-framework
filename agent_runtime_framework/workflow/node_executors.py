from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime_framework.models import ChatRequest, chat_once, resolve_model_runtime

from agent_runtime_framework.workflow.aggregator import aggregate_node_results
from agent_runtime_framework.workflow.conversation import build_conversation_messages
from agent_runtime_framework.workflow.llm_access import get_application_context, get_workspace_context, get_workspace_root
from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.memory_views import build_response_memory_view_from_payload
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike, WorkflowNodeExecutor


@dataclass(slots=True)
class ConversationResponseExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        application_context = get_application_context(context)
        workspace_context = get_workspace_context(context)
        session = getattr(workspace_context, "session", None) if workspace_context is not None else None
        reply = _generate_conversation_reply(run.goal, application_context, session=session, context=workspace_context)
        run.final_output = reply
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"summary": reply, "final_response": reply}, references=[])


def _generate_conversation_reply(user_input: str, application_context: Any, *, session: Any = None, context: Any = None) -> str:
    if application_context is None:
        raise RuntimeError("missing application context for conversation response")
    runtime = resolve_model_runtime(application_context, "conversation")
    llm_client = runtime.client if runtime is not None else application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else application_context.llm_model
    if llm_client is None or not model_name:
        raise RuntimeError("llm_unavailable: 未配置可用模型用于 conversation response")
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=build_conversation_messages(user_input, session, context=context),
                temperature=0.3,
                max_tokens=1024,
            ),
        )
    except Exception as exc:
        raise RuntimeError(f"conversation response failed: {type(exc).__name__}: {exc}") from exc
    content = str(response.content or "").strip()
    if not content:
        raise RuntimeError("conversation response returned empty content")
    return content


@dataclass(slots=True)
class AggregationExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        node_results = run.shared_state.get("node_results", {})
        ordered_results = [
            result
            for key, result in node_results.items()
            if key != node.node_id and result.status == NODE_STATUS_COMPLETED
        ]
        aggregated = aggregate_node_results(ordered_results)
        run.shared_state["aggregated_result"] = aggregated
        return aggregated


@dataclass(slots=True)
class VerificationExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        node_results = run.shared_state.get("node_results", {})
        verification_events: list[dict[str, Any]] = []
        references: list[str] = []
        for key, result in node_results.items():
            if key == node.node_id:
                continue
            if isinstance(result.output, dict):
                events = result.output.get("verification_events")
                if isinstance(events, list):
                    for event in events:
                        if isinstance(event, dict):
                            verification_events.append(event)
                verification = result.output.get("verification")
                if isinstance(verification, dict) and verification not in verification_events:
                    verification_events.append(verification)
            for reference in result.references:
                if reference not in references:
                    references.append(reference)
        if not verification_events:
            verification_events = self._active_verification_events(node_results, context, verification_type=str(node.metadata.get("verification_type") or "").strip())
        if not verification_events:
            summary = str(node.metadata.get("verification_summary") or "No verification result was produced.")
            verification = {"status": "failed", "success": False, "summary": summary}
            return NodeResult(
                status=NODE_STATUS_FAILED,
                output={
                    "summary": summary,
                    "verification": verification,
                    "verification_events": [],
                    "quality_signals": [
                        {
                            "source": "verification",
                            "relevance": "high",
                            "confidence": 0.9,
                            "progress_contribution": "verification_blocked",
                            "verification_needed": False,
                            "recoverable_error": True,
                        }
                    ],
                    "reasoning_trace": [{"kind": "verification_summary", "summary": summary}],
                },
                references=references,
                error=summary,
            )
        verification_by_type: dict[str, dict[str, Any]] = {}
        for event in verification_events:
            verification_type = str(event.get("verification_type") or "general").strip() or "general"
            bucket = verification_by_type.setdefault(verification_type, {"status": "passed", "success": True, "summary": "", "events": []})
            bucket["events"].append(event)
            event_success = bool(event.get("success", event.get("status") == "passed"))
            event_status = str(event.get("status") or ("passed" if event_success else "failed"))
            if event_status == "failed" or event_success is False and event_status != "not_run":
                bucket["status"] = "failed"
                bucket["success"] = False
            bucket["summary"] = str(event.get("summary") or bucket.get("summary") or "").strip()

        failed_events = [event for event in verification_events if not bool(event.get("success", event.get("status") == "passed"))]
        if failed_events:
            summary = str(failed_events[-1].get("summary") or "Verification failed.")
            verification = {"status": "failed", "success": False, "summary": summary}
            return NodeResult(
                status=NODE_STATUS_FAILED,
                output={
                    "summary": summary,
                    "verification": verification,
                    "verification_events": verification_events,
                    "verification_by_type": verification_by_type,
                    "quality_signals": [
                        {
                            "source": "verification",
                            "relevance": "high",
                            "confidence": 0.95,
                            "progress_contribution": "verification_failed",
                            "verification_needed": False,
                            "recoverable_error": True,
                        }
                    ],
                    "reasoning_trace": [{"kind": "verification_summary", "summary": summary}],
                },
                references=references,
                error=summary,
            )

        summaries = [str(event.get("summary") or "").strip() for event in verification_events if str(event.get("summary") or "").strip()]
        summary = "；".join(summaries) or "Verification completed."
        verification = {"status": "passed", "success": True, "summary": summary}
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": summary,
                "verification": verification,
                "verification_events": verification_events,
                "verification_by_type": verification_by_type,
                "quality_signals": [
                    {
                        "source": "verification",
                        "relevance": "high",
                        "confidence": 0.95,
                        "progress_contribution": "verification_completed",
                        "verification_needed": False,
                        "recoverable_error": False,
                    }
                ],
                "reasoning_trace": [{"kind": "verification_summary", "summary": summary}],
            },
            references=references,
            error=None,
        )

    def _active_verification_events(
        self,
        node_results: dict[str, NodeResult],
        context: RuntimeContextLike,
        *,
        verification_type: str,
    ) -> list[dict[str, Any]]:
        workspace_root = Path(get_workspace_root(context, ".")).expanduser()
        active_types = {verification_type, "post_write", "post_change", "completion", ""}
        events: list[dict[str, Any]] = []
        for result in node_results.values():
            output = result.output if isinstance(getattr(result, "output", None), dict) else {}
            tool_name = str(output.get("tool_name") or "").strip()
            if tool_name not in {
                "create_workspace_path",
                "move_workspace_path",
                "delete_workspace_path",
                "apply_text_patch",
                "edit_workspace_text",
                "append_workspace_text",
            }:
                continue
            arguments = dict(output.get("arguments") or {})
            requested_path = str(arguments.get("path") or "").strip()
            destination_path = str(arguments.get("destination_path") or "").strip()
            expected_content = str(arguments.get("content") or arguments.get("replace_text") or "").strip()
            search_text = str(arguments.get("search_text") or "").strip()
            if tool_name == "move_workspace_path" and destination_path:
                target = workspace_root / destination_path
                success = target.exists()
                summary = f"Verified moved path {destination_path}" if success else f"Moved path missing: {destination_path}"
                events.append({"status": "passed" if success else "failed", "success": success, "summary": summary, "verification_type": verification_type or "post_change"})
                continue
            if not requested_path:
                continue
            target = workspace_root / requested_path
            if tool_name == "delete_workspace_path":
                success = not target.exists()
                summary = f"Verified deleted path {requested_path}" if success else f"Deleted path still exists: {requested_path}"
                events.append({"status": "passed" if success else "failed", "success": success, "summary": summary, "verification_type": verification_type or "post_change"})
                continue
            if tool_name == "create_workspace_path":
                success = target.exists()
                if success and expected_content:
                    success = target.read_text(encoding="utf-8") == expected_content
                summary = f"Verified created path {requested_path}" if success else f"Created path verification failed: {requested_path}"
                events.append({"status": "passed" if success else "failed", "success": success, "summary": summary, "verification_type": verification_type or "post_write"})
                continue
            if tool_name in {"edit_workspace_text", "append_workspace_text", "apply_text_patch"}:
                if not target.exists():
                    events.append({"status": "failed", "success": False, "summary": f"Target file missing: {requested_path}", "verification_type": verification_type or "post_write"})
                    continue
                content = target.read_text(encoding="utf-8")
                success = True
                if tool_name == "edit_workspace_text" and expected_content:
                    success = content == expected_content
                elif tool_name == "append_workspace_text" and expected_content:
                    success = content.endswith(expected_content)
                elif tool_name == "apply_text_patch":
                    success = (not search_text or search_text not in content) and (not expected_content or expected_content in content)
                summary = f"Verified file update {requested_path}" if success else f"File update verification failed: {requested_path}"
                events.append({"status": "passed" if success else "failed", "success": success, "summary": summary, "verification_type": verification_type or "post_write"})
        return [event for event in events if event.get("verification_type") in active_types]


@dataclass(slots=True)
class ApprovalGateExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        summary = str(node.metadata.get("approval_summary") or "Approval gate passed.")
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"summary": summary},
            references=[],
        )


@dataclass(slots=True)
class FinalResponseExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        judge_decision = dict(run.shared_state.get("judge_decision") or {})
        judge_status = str(judge_decision.get("status") or "").strip()
        if judge_status and judge_status != "accepted":
            error = f"judge blocked final response: {judge_status}"
            return NodeResult(status=NODE_STATUS_FAILED, error=error, output={"summary": error}, references=[])
        conversation_mode = bool(node.metadata.get("conversation_mode") or run.graph.metadata.get("conversation_mode") or run.shared_state.get("conversation_mode"))
        aggregated = run.shared_state.get("aggregated_result")
        if aggregated is None:
            node_results = run.shared_state.get("node_results", {})
            direct_results = [
                result
                for key, result in node_results.items()
                if key != node.node_id and result.status == NODE_STATUS_COMPLETED
            ]
            aggregated = aggregate_node_results(direct_results)
        synthesized = dict(run.shared_state.get("evidence_synthesis") or {})
        final_response = str(synthesized.get("final_response") or synthesized.get("summary") or "").strip()
        if conversation_mode and not final_response:
            application_context = get_application_context(context)
            workspace_context = get_workspace_context(context)
            session = getattr(workspace_context, "session", None) if workspace_context is not None else None
            final_response = _generate_conversation_reply(run.goal, application_context, session=session, context=workspace_context)
        if not final_response:
            summaries = aggregated.output.get("summaries", []) if aggregated else []
            facts = aggregated.output.get("facts", []) if aggregated and isinstance(aggregated.output, dict) else []
            evidence_items = aggregated.output.get("evidence_items", []) if aggregated and isinstance(aggregated.output, dict) else []
            verification = aggregated.output.get("verification") if aggregated and isinstance(aggregated.output, dict) else None
            response_memory_view = build_response_memory_view_from_payload(run.shared_state.get("memory_state"))
            final_response = synthesize_text(
                context,
                role="composer",
                system_prompt=(
                    "You write the final workflow answer for an end user. "
                    "Use the provided evidence, summaries, and verification state. Keep the answer direct, natural, and non-repetitive."
                ),
                payload={
                    "goal": run.goal,
                    "summaries": summaries,
                    "facts": facts,
                    "evidence_items": evidence_items,
                    "verification": verification,
                    "references": list(aggregated.references if aggregated else []),
                    "response_memory_view": response_memory_view,
                },
                max_tokens=320,
            ) or ""
        if not final_response:
            raise RuntimeError("final_response_model_unavailable: no model-generated final response available")
        result = NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"final_response": final_response},
            references=list(aggregated.references if aggregated else []),
        )
        run.final_output = final_response
        return result
