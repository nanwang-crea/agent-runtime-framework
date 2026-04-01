from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_runtime_framework.agents.workspace_backend.prompting import render_workspace_prompt_doc
from agent_runtime_framework.agents.workspace_backend.run_context import build_run_context_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime

from agent_runtime_framework.workflow.aggregator import aggregate_node_results
from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


class NodeExecutor(Protocol):
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult: ...


@dataclass(slots=True)
class ConversationResponseExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        runtime_context = dict(context or {})
        application_context = runtime_context.get("application_context")
        workspace_context = runtime_context.get("workspace_context")
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
                messages=_build_conversation_messages(user_input, session, context=context),
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


def _build_conversation_messages(user_input: str, session: Any, context: Any | None = None) -> list[ChatMessage]:
    system_content = render_workspace_prompt_doc("conversation_system")
    if context is not None:
        system_content += "\n\n" + build_run_context_block(context, session=session, user_input=user_input)
    messages = [ChatMessage(role="system", content=system_content)]
    recent_turns = list(getattr(session, "turns", [])[-6:]) if session is not None else []
    if recent_turns:
        last_turn = recent_turns[-1]
        if getattr(last_turn, "role", None) == "user" and getattr(last_turn, "content", "") == user_input:
            recent_turns = recent_turns[:-1]
    for turn in recent_turns:
        messages.append(ChatMessage(role=turn.role, content=turn.content))
    messages.append(ChatMessage(role="user", content=user_input))
    return messages


@dataclass(slots=True)
class WorkspaceOverviewExecutor:
    max_entries: int = 50

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        workspace_root = Path(str((context or {}).get("workspace_root", ".")))
        entries = []
        references = []
        for path in sorted(workspace_root.iterdir(), key=lambda item: item.name)[: self.max_entries]:
            label = f"{path.name}/" if path.is_dir() else path.name
            entries.append(label)
            references.append(str(path))
            if path.is_dir():
                for child in sorted(path.iterdir(), key=lambda item: item.name)[:3]:
                    child_label = f"{path.name}/{child.name}/" if child.is_dir() else f"{path.name}/{child.name}"
                    entries.append(child_label)
                    references.append(str(child))
        summary = synthesize_text(
            context,
            role="composer",
            system_prompt=(
                "You summarize a workspace overview for an end user. "
                "Respond with one concise natural-language paragraph in the user's language."
            ),
            payload={"goal": run.goal, "workspace_root": str(workspace_root), "entries": entries},
            max_tokens=180,
        ) or ", ".join(entries[:5])
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"workspace_root": str(workspace_root), "entries": entries, "summary": summary},
            references=references,
        )


@dataclass(slots=True)
class FileReadExecutor:
    max_chars: int = 4000

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        workspace_root = Path(str((context or {}).get("workspace_root", ".")))
        target_path = str(node.metadata.get("target_path") or "")
        if not target_path:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing target_path")

        path = workspace_root / target_path
        content = path.read_text(encoding="utf-8")
        truncated = len(content) > self.max_chars
        visible_content = content[: self.max_chars]
        summary = visible_content[:200]
        if truncated:
            visible_content = f"{visible_content.rstrip()}\n...[已截断]"
            summary = f"{summary.rstrip()} ...[已截断]"
        summary = synthesize_text(
            context,
            role="composer",
            system_prompt=(
                "You summarize file contents for an end user. "
                "Focus on what the file is about and keep the answer concise in the user's language."
            ),
            payload={"goal": run.goal, "path": target_path, "content": visible_content},
            max_tokens=220,
        ) or summary
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"path": target_path, "content": visible_content, "summary": summary},
            references=[str(path)],
        )


@dataclass(slots=True)
class AggregationExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
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
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        node_results = run.shared_state.get("node_results", {})
        latest_verification: dict[str, Any] | None = None
        references: list[str] = []
        for key, result in node_results.items():
            if key == node.node_id:
                continue
            if isinstance(result.output, dict):
                verification = result.output.get("verification")
                if isinstance(verification, dict):
                    latest_verification = verification
            for reference in result.references:
                if reference not in references:
                    references.append(reference)
        if latest_verification is None:
            summary = str(node.metadata.get("verification_summary") or "No explicit verification result was produced.")
            return NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={"summary": summary, "verification": {"success": True, "summary": summary}},
                references=references,
            )
        success = bool(latest_verification.get("success", False))
        summary = str(latest_verification.get("summary") or "Verification completed.")
        return NodeResult(
            status=NODE_STATUS_COMPLETED if success else NODE_STATUS_FAILED,
            output={"summary": summary, "verification": latest_verification},
            references=references,
            error=None if success else summary,
        )


@dataclass(slots=True)
class ApprovalGateExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        summary = str(node.metadata.get("approval_summary") or "Approval gate passed.")
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"summary": summary},
            references=[],
        )


@dataclass(slots=True)
class FinalResponseExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        aggregated = run.shared_state.get("aggregated_result")
        if aggregated is None:
            node_results = run.shared_state.get("node_results", {})
            direct_results = [
                result
                for key, result in node_results.items()
                if key != node.node_id and result.status == NODE_STATUS_COMPLETED
            ]
            aggregated = aggregate_node_results(direct_results)
        synthesized = dict(run.shared_state.get("response_synthesis") or {})
        final_response = str(synthesized.get("final_response") or synthesized.get("summary") or "").strip()
        if not final_response:
            summaries = aggregated.output.get("summaries", []) if aggregated else []
            final_response = synthesize_text(
                context,
                role="composer",
                system_prompt=(
                    "You write the final workflow answer for an end user. "
                    "Use the provided summaries and keep the answer direct, natural, and non-repetitive."
                ),
                payload={"goal": run.goal, "summaries": summaries, "references": list(aggregated.references if aggregated else [])},
                max_tokens=320,
            ) or "\n".join(str(item) for item in summaries if item)
        result = NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"final_response": final_response},
            references=list(aggregated.references if aggregated else []),
        )
        run.final_output = final_response
        return result
