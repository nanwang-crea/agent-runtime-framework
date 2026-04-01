from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import logging
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from agent_runtime_framework.agents import AgentRegistry, builtin_agent_definitions
from agent_runtime_framework.agents.workspace_backend import WorkspaceAction, WorkspaceContext, build_default_workspace_tools
from agent_runtime_framework.agents.workspace_backend.personas import resolve_runtime_persona
from agent_runtime_framework.agents.workspace_backend.models import EvidenceItem, TaskState, WorkspaceTask
from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.session import AssistantSession
from agent_runtime_framework.memory import InMemorySessionMemory
from agent_runtime_framework.memory.index import MemoryRecord
from agent_runtime_framework.runtime import AgentRuntime
from agent_runtime_framework.models import (
    CodexCliDriver,
    InMemoryCredentialStore,
    ModelRegistry,
    ModelRouter,
    OpenAICompatibleDriver,
    ChatRequest,
    chat_once,
    chat_stream,
    resolve_model_runtime,
)
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository, ResourceRef
from agent_runtime_framework.sandbox import SandboxConfig, resolve_sandbox
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.demo.model_center import ModelCenterService, ModelCenterStore
from agent_runtime_framework.core.errors import AppError, log_app_error, normalize_app_error
from agent_runtime_framework.workflow import WorkflowRuntime, analyze_goal, build_workflow_graph
from agent_runtime_framework.workflow.conversation import build_conversation_messages
from agent_runtime_framework.workflow.discovery_executor import WorkspaceDiscoveryExecutor
from agent_runtime_framework.workflow.content_search_executor import ContentSearchExecutor
from agent_runtime_framework.workflow.chunked_file_read_executor import ChunkedFileReadExecutor
from agent_runtime_framework.workflow.evidence_synthesis_executor import EvidenceSynthesisExecutor
from agent_runtime_framework.workflow.node_executors import AggregationExecutor, ApprovalGateExecutor, ConversationResponseExecutor, FinalResponseExecutor, VerificationExecutor
from agent_runtime_framework.workflow.tool_call_executor import ToolCallExecutor
from agent_runtime_framework.workflow.clarification_executor import ClarificationExecutor
from agent_runtime_framework.workflow.target_resolution_executor import TargetResolutionExecutor
from agent_runtime_framework.workflow.workspace_subtask import WorkspaceSubtaskExecutor

logger = logging.getLogger(__name__)


def _format_error_detail(exc: Exception) -> str:
    detail = f"{type(exc).__name__}: {exc}".strip()
    detail = " ".join(detail.split())
    return detail[:240]



def stream_conversation_reply(user_input: str, context: Any, session: Any, *, diagnostics: dict[str, str | None] | None = None) -> Iterable[str]:
    meta = diagnostics if diagnostics is not None else {}
    meta["source"] = "model"
    meta["reason"] = "pending"
    runtime = resolve_model_runtime(context.application_context, "conversation")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        raise RuntimeError("llm_unavailable: 未配置可用模型用于 conversation response")
    messages = build_conversation_messages(user_input, session, context=context)
    try:
        response = chat_stream(llm_client, ChatRequest(model=model_name, messages=messages, temperature=0.3, max_tokens=1024))
        streamed = False
        for chunk in response:
            streamed = True
            if chunk.content:
                yield chunk.content
        if streamed:
            meta["source"] = "model"
            meta["reason"] = "stream"
            return
    except Exception as exc:
        meta["reason"] = f"stream_error:{_format_error_detail(exc)}"
    try:
        response = chat_once(llm_client, ChatRequest(model=model_name, messages=messages, temperature=0.3, max_tokens=1024))
        content = str(response.content or "").strip()
        if content:
            meta["source"] = "model"
            meta["reason"] = "non_stream_fallback"
            yield content
            return
    except Exception as exc:
        meta["reason"] = f"model_error:{_format_error_detail(exc)}"
        raise RuntimeError(meta["reason"]) from exc
    raise RuntimeError("conversation response returned empty content")


@dataclass(slots=True)
class DemoAssistantApp:
    workspace: Path
    context: WorkspaceContext
    _pending_workflow_clarification: dict[str, Any] | None
    model_registry: ModelRegistry
    model_router: ModelRouter
    model_center: ModelCenterService
    _pending_tokens: dict[str, Any]
    _run_history: list[dict[str, Any]]
    _task_history: list[Any]
    _run_inputs: dict[str, str]
    _last_route_decision: dict[str, str] | None
    _active_agent: str
    _available_workspaces: list[str]
    agent_registry: AgentRegistry
    agent_runtime: AgentRuntime

    def chat(self, message: str) -> dict[str, Any]:
        try:
            self._ensure_session()
            payload = self._run_workflow(message)
            self._record_run(payload, prompt=message)
            return payload
        except Exception as exc:
            return self._error_payload(exc)


    def _ensure_session(self) -> AssistantSession:
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        return session

    def _compile_workflow(self, message: str) -> tuple[Any, Any]:
        route_source = "clarification" if self._pending_workflow_clarification is not None else "goal_analysis"
        self._last_route_decision = {"route": "workflow", "source": route_source}
        goal = analyze_goal(message, context=self.context)
        graph = build_workflow_graph(goal, context=self.context)
        return goal, graph

    def _run_workflow(self, message: str) -> dict[str, Any]:
        _goal, graph = self._compile_workflow(message)
        return self._run_compiled_workflow(message, graph=graph)

    def _run_compiled_workflow(self, message: str, *, graph: Any) -> dict[str, Any]:
        runtime = self._build_workflow_runtime()
        from agent_runtime_framework.workflow import WorkflowRun

        run = WorkflowRun(goal=message, graph=graph)
        run.shared_state["memory"] = self.memory_payload()
        run.shared_state["session_memory_snapshot"] = self.context.application_context.session_memory.snapshot()
        run = runtime.run(run)
        self._remember_workflow_run(message, run)
        self._capture_workflow_codex_history(run)
        execution_trace = [
            {"name": node.node_id, "status": run.node_states[node.node_id].status, "detail": node.node_type}
            for node in graph.nodes
            if node.node_id in run.node_states
        ]
        approval_request = None
        resume_token_id = None
        if run.status == "waiting_approval":
            resume_token = run.shared_state.get("resume_token")
            if resume_token is not None:
                self._pending_tokens[resume_token.token_id] = {"kind": "workflow", "runtime": runtime, "run": run, "token": resume_token}
                resume_token_id = resume_token.token_id
            approval_request = self._workflow_approval_request(run)
        clarification_request = run.shared_state.get("clarification_request")
        payload_status = "needs_clarification" if clarification_request is not None and run.status == "completed" else run.status
        self._pending_workflow_clarification = dict(clarification_request or {}) if payload_status == "needs_clarification" else None
        final_answer = str(run.final_output or (clarification_request or {}).get("prompt") or (approval_request or {}).get("reason") or "")
        evidence = self._workflow_evidence_payload(run)
        return {
            "status": payload_status,
            "run_id": run.run_id,
            "plan_id": run.run_id,
            "final_answer": final_answer,
            "capability_name": ("conversation" if any(node.node_type == "conversation_response" for node in graph.nodes) else "workflow"),
            "runtime": "workflow",
            "execution_trace": self._with_router_trace(execution_trace),
            "evidence": evidence,
            "approval_request": approval_request,
            "resume_token_id": resume_token_id,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": str(self.workspace),
        }

    def stream_chat(self, message: str, *, chunk_size: int = 24):
        yield {"type": "start", "message": message}
        session = self._ensure_session()
        yield {"type": "status", "status": {"phase": "routing", "label": "正在规划下一步动作"}}
        _goal, graph = self._compile_workflow(message)
        if len(graph.nodes) == 1 and graph.nodes[0].node_type == "conversation_response":
            try:
                yield from self._stream_workflow_conversation(message, session)
            except Exception as exc:
                payload = self._error_payload(exc)
                yield {"type": "error", "error": dict(payload.get("error") or {})}
            return
        yield {"type": "status", "status": {"phase": "execution", "label": "正在执行工作流"}}
        try:
            payload = self._run_compiled_workflow(message, graph=graph)
            self._record_run(payload, prompt=message)
        except Exception as exc:
            payload = self._error_payload(exc)
            yield {"type": "error", "error": dict(payload.get("error") or {})}
            return
        if payload.get("status") == "error":
            yield {"type": "error", "error": dict(payload.get("error") or {})}
            return
        for step in payload.get("execution_trace", []):
            yield {"type": "step", "step": step}
        yield {"type": "memory", "memory": self.memory_payload()}
        final_answer = str(payload.get("final_answer") or "")
        if not final_answer:
            yield {"type": "final", "payload": payload}
            return
        yield {"type": "delta", "delta": final_answer}
        yield {"type": "final", "payload": payload}

    def _stream_workflow_conversation(self, message: str, session: AssistantSession):
        session.add_turn("user", message)
        yield {"type": "status", "status": {"phase": "execution", "label": "正在执行工作流"}}
        yield {"type": "step", "step": {"name": "conversation_response", "status": "running", "detail": "conversation_response"}}
        chunks: list[str] = []
        diagnostics: dict[str, str | None] = {"source": "fallback", "reason": "unknown"}
        for chunk in stream_conversation_reply(message, self.context, session, diagnostics=diagnostics):
            if not chunk:
                continue
            chunks.append(chunk)
            yield {"type": "delta", "delta": chunk}
        final_answer = "".join(chunks).strip()
        session.add_turn("assistant", final_answer)
        session.focused_capability = "workflow"
        task = type("WorkflowConversationTask", (), {})()
        task.task_id = str(uuid4())
        task.goal = message
        task.actions = [type("WorkflowConversationAction", (), {"kind": "conversation_response", "instruction": message, "status": "completed", "observation": final_answer, "metadata": {}})()]
        self._task_history.insert(0, task)
        self._task_history = self._task_history[:40]
        source = str(diagnostics.get("source") or "fallback")
        reason = str(diagnostics.get("reason") or "")
        payload = {
            "status": "completed",
            "run_id": str(uuid4()),
            "plan_id": task.task_id,
            "final_answer": final_answer,
            "capability_name": "conversation",
            "runtime": "workflow",
            "execution_trace": [{"name": "conversation_response", "status": "completed", "detail": f"source={source}; reason={reason}" if reason else f"source={source}"}],
            "approval_request": None,
            "resume_token_id": None,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": str(self.workspace),
        }
        self._record_run(payload, prompt=message)
        yield {"type": "memory", "memory": payload["memory"]}
        yield {"type": "final", "payload": payload}

    def approve(self, token_id: str, approved: bool) -> dict[str, Any]:
        token = self._pending_tokens.pop(token_id, None)
        if token is None:
            return {
                "status": "missing_token",
                "final_answer": "未找到可恢复的审批请求。",
                "capability_name": "",
                "execution_trace": [],
                "session": self.session_payload(),
                "plan_history": self.plan_history_payload(),
                "run_history": self.run_history_payload(),
                "memory": self.memory_payload(),
                "approval_request": None,
                "resume_token_id": None,
                "workspace": str(self.workspace),
            }
        if isinstance(token, dict) and token.get("kind") == "workflow":
            runtime = token["runtime"]
            run = token["run"]
            resume_token = token["token"]
            resumed = runtime.resume(run, resume_token=resume_token, approved=approved)
            self._remember_workflow_run(f"approval:{'approve' if approved else 'reject'}", resumed)
            self._capture_workflow_codex_history(resumed)
            payload = self._workflow_payload(resumed)
            self._record_run(payload, prompt=f"approval:{'approve' if approved else 'reject'}")
            return payload
        return {
            "status": "missing_token",
            "final_answer": "未找到可恢复的审批请求。",
            "capability_name": "",
            "execution_trace": [],
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "approval_request": None,
            "resume_token_id": None,
            "workspace": str(self.workspace),
        }

    def replay(self, run_id: str) -> dict[str, Any]:
        prompt = self._run_inputs.get(run_id)
        if not prompt:
            return {
                "status": "missing_run",
                "final_answer": "未找到可重放的运行记录。",
                "capability_name": "",
                "execution_trace": [],
                "session": self.session_payload(),
                "plan_history": self.plan_history_payload(),
                "run_history": self.run_history_payload(),
                "memory": self.memory_payload(),
                "approval_request": None,
                "resume_token_id": None,
                "workspace": str(self.workspace),
            }
        payload = self.chat(prompt)
        self._record_run(payload, prompt=f"replay:{run_id}")
        return payload

    def context_payload(self) -> dict[str, Any]:
        return {
            "active_agent": self._active_agent,
            "active_persona": self._active_persona_name(),
            "available_agents": [definition.to_payload() for definition in self.agent_registry.list()],
            "active_workspace": str(self.workspace),
            "available_workspaces": list(dict.fromkeys([str(self.workspace), *self._available_workspaces])),
            "sandbox": resolve_sandbox(self.context).to_payload(),
        }

    def switch_context(self, *, agent_profile: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        if agent_profile:
            if self.agent_registry.get(agent_profile) is None:
                raise ValueError(f"unknown agent profile: {agent_profile}")
            self._active_agent = agent_profile
            self.context.services["active_agent"] = agent_profile
        if workspace:
            next_workspace = Path(workspace).expanduser().resolve()
            if not next_workspace.exists():
                raise FileNotFoundError(next_workspace)
            self.workspace = next_workspace
            self.context.application_context.resource_repository = LocalFileResourceRepository([next_workspace])
            self.context.application_context.config["default_directory"] = str(next_workspace)
            sandbox = self.context.application_context.services.get("sandbox")
            if isinstance(sandbox, SandboxConfig):
                sandbox.workspace_root = next_workspace
                sandbox.writable_roots = [next_workspace]
            self._available_workspaces = list(dict.fromkeys([str(next_workspace), *self._available_workspaces]))
        return {
            "workspace": str(self.workspace),
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
        }


    def _active_persona_name(self) -> str:
        session = self.context.session
        if session is not None and session.active_persona:
            return session.active_persona
        return resolve_runtime_persona(self.context).name

    def session_payload(self) -> dict[str, Any]:
        session = self.context.session
        if session is None:
            return {"session_id": None, "turns": []}
        return {
            "session_id": session.session_id,
            "turns": [
                {"role": turn.role, "content": turn.content}
                for turn in session.turns
            ],
        }

    def _workflow_payload(self, run: Any) -> dict[str, Any]:
        execution_trace = [
            {"name": node.node_id, "status": run.node_states[node.node_id].status, "detail": node.node_type}
            for node in run.graph.nodes
            if node.node_id in run.node_states
        ]
        approval_request = None
        resume_token_id = None
        if run.status == "waiting_approval":
            resume_token = run.shared_state.get("resume_token")
            if resume_token is not None:
                self._pending_tokens[resume_token.token_id] = {"kind": "workflow", "runtime": self._build_workflow_runtime(), "run": run, "token": resume_token}
                resume_token_id = resume_token.token_id
            approval_request = self._workflow_approval_request(run)
        clarification_request = run.shared_state.get("clarification_request")
        payload_status = "needs_clarification" if clarification_request is not None and run.status == "completed" else run.status
        self._pending_workflow_clarification = dict(clarification_request or {}) if payload_status == "needs_clarification" else None
        final_answer = str(run.final_output or (clarification_request or {}).get("prompt") or (approval_request or {}).get("reason") or "")
        evidence = self._workflow_evidence_payload(run)
        return {
            "status": payload_status,
            "run_id": run.run_id,
            "plan_id": run.run_id,
            "final_answer": final_answer,
            "capability_name": ("conversation" if any(node.node_type == "conversation_response" for node in run.graph.nodes) else "workflow"),
            "runtime": "workflow",
            "execution_trace": self._with_router_trace(execution_trace),
            "evidence": evidence,
            "approval_request": approval_request,
            "resume_token_id": resume_token_id,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": str(self.workspace),
        }


    def _workflow_evidence_payload(self, run: Any) -> dict[str, Any]:
        node_results = run.shared_state.get("node_results", {})
        aggregated = run.shared_state.get("aggregated_result")
        aggregated_output = aggregated.output if isinstance(getattr(aggregated, "output", None), dict) else {}
        synthesized = dict(run.shared_state.get("response_synthesis") or {})
        candidates: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        verification = dict(synthesized.get("verification") or aggregated_output.get("verification") or {})
        if not verification:
            verification = {"status": "not_run", "success": False, "summary": "No explicit verification result was produced."}
        for result in node_results.values():
            if not isinstance(getattr(result, "output", None), dict):
                continue
            output = result.output
            for item in output.get("candidates", output.get("matches", [])) or []:
                if isinstance(item, dict) and item not in candidates:
                    candidates.append(item)
            for chunk in output.get("chunks", []) or []:
                if isinstance(chunk, dict) and chunk not in chunks:
                    chunks.append(chunk)
            if not verification and isinstance(output.get("verification"), dict):
                verification = dict(output.get("verification") or {})
        if not chunks:
            for chunk in synthesized.get("chunks", []) or aggregated_output.get("chunks", []) or []:
                if isinstance(chunk, dict) and chunk not in chunks:
                    chunks.append(chunk)
        return {
            "candidates": candidates,
            "evidence_items": list(synthesized.get("evidence_items") or aggregated_output.get("evidence_items") or []),
            "chunks": chunks,
            "facts": list(synthesized.get("facts") or aggregated_output.get("facts") or []),
            "open_questions": list(synthesized.get("open_questions") or aggregated_output.get("open_questions") or []),
            "verification": verification,
        }

    def _workflow_runtime_context(self) -> dict[str, Any]:
        return {
            "workspace_root": str(self.workspace),
            "application_context": self.context.application_context,
            "workspace_context": self.context,
            "memory": self.memory_payload(),
            "session_memory_snapshot": self.context.application_context.session_memory.snapshot(),
        }

    def _build_workflow_runtime(self) -> WorkflowRuntime:
        return WorkflowRuntime(
            executors={
                "conversation_response": ConversationResponseExecutor(),
                "workspace_discovery": WorkspaceDiscoveryExecutor(),
                "content_search": ContentSearchExecutor(),
                "chunked_file_read": ChunkedFileReadExecutor(),
                "evidence_synthesis": EvidenceSynthesisExecutor(),
                "aggregate_results": AggregationExecutor(),
                "verification": VerificationExecutor(),
                "approval_gate": ApprovalGateExecutor(),
                "final_response": FinalResponseExecutor(),
                "tool_call": ToolCallExecutor(),
                "clarification": ClarificationExecutor(),
                "target_resolution": TargetResolutionExecutor(),
                "workspace_subtask": WorkspaceSubtaskExecutor(run_subtask=self._run_workspace_subtask),
            },
            context=self._workflow_runtime_context(),
        )

    def _run_workspace_subtask(self, goal: str, *, task_profile: str, metadata: dict[str, Any]):
        summary = str(metadata.get("summary") or goal)
        target_path = str(metadata.get("target_path") or metadata.get("path") or "").strip()
        state = TaskState()
        if target_path:
            state.resolved_target = target_path
            state.evidence_items.append(EvidenceItem(source="workflow", kind="path", summary=target_path, path=target_path))
        action = WorkspaceAction(kind="workspace_subtask", instruction=goal, status="completed", observation=summary, metadata={"direct_output": True})
        task = WorkspaceTask(goal=goal, actions=[action], task_profile=task_profile, state=state)
        task.summary = summary
        from agent_runtime_framework.workflow.workspace_subtask import WorkspaceSubtaskResult

        return WorkspaceSubtaskResult(status="completed", final_output=summary, task=task, action_kind="workspace_subtask", run_id=str(uuid4()))

    def _workflow_approval_request(self, run: Any) -> dict[str, Any] | None:
        resume_token = run.shared_state.get("resume_token")
        if resume_token is None:
            return None
        state = run.node_states.get(resume_token.node_id)
        if state is None or state.result is None:
            return {"capability_name": "approval_gate", "instruction": "Review workflow step", "reason": "需要审批后继续执行工作流。", "risk_class": "medium"}
        approval_data = dict(state.result.approval_data or {})
        request = approval_data.get("approval_request")
        if request is not None:
            return {
                "capability_name": request.capability_name,
                "instruction": request.instruction,
                "reason": request.reason,
                "risk_class": request.risk_class,
            }
        return {
            "capability_name": state.node_id if hasattr(state, "node_id") else "approval_gate",
            "instruction": str(state.result.output.get("summary") if isinstance(state.result.output, dict) else "Review workflow step"),
            "reason": "需要审批后继续执行工作流。",
            "risk_class": "medium",
        }

    def _capture_workflow_codex_history(self, run: Any) -> None:
        results = run.shared_state.get("workspace_subtask_results", {})
        for result in results.values():
            task = getattr(result, "task", None)
            if task is None:
                continue
            self._task_history.insert(0, task)
        self._task_history = self._task_history[:40]

    def _remember_workflow_run(self, message: str, run: Any) -> None:
        session = self.context.session
        if session is not None:
            session.add_turn("user", message)
            if run.final_output:
                session.add_turn("assistant", str(run.final_output))
            session.focused_capability = "workflow"
        pseudo_actions = []
        references: list[str] = []
        node_results = run.shared_state.get("node_results", {})
        for node in run.graph.nodes:
            state = run.node_states.get(node.node_id)
            result = node_results.get(node.node_id)
            observation = ""
            if result is not None:
                if isinstance(result.output, dict):
                    observation = str(result.output.get("summary") or result.output.get("final_response") or result.output.get("content") or "")
                elif result.output is not None:
                    observation = str(result.output)
                for reference in getattr(result, "references", []):
                    if reference and reference not in references:
                        references.append(reference)
            pseudo_actions.append(SimpleNamespace(kind=node.node_type, instruction=message, status=getattr(state, "status", "pending"), observation=observation, metadata={}))
        workflow_task = SimpleNamespace(task_id=run.run_id, goal=message, actions=pseudo_actions)
        self._task_history.insert(0, workflow_task)
        self._task_history = self._task_history[:40]
        if references:
            ref = ResourceRef.for_path(references[0])
            summary = str(run.final_output or f"Workflow completed for {ref.title}")
            self.context.application_context.session_memory.remember_focus([ref], summary=summary)
            remember = getattr(self.context.application_context.index_memory, "remember", None)
            if callable(remember):
                path = str(Path(ref.location).resolve().relative_to(self.workspace)) if Path(ref.location).resolve().is_relative_to(self.workspace) else ref.location
                remember(MemoryRecord(key=f"focus:{path}", text=f"{path} {summary}".strip(), kind="workspace_focus", metadata={"path": path, "summary": summary}))

    def memory_payload(self) -> dict[str, Any]:
        snapshot = self.context.application_context.session_memory.snapshot()
        focused_resources = list(snapshot.focused_resources)
        return {
            "focused_resource": self._resource_payload(focused_resources[0]) if focused_resources else None,
            "recent_resources": [self._resource_payload(resource) for resource in focused_resources[:5]],
            "last_summary": snapshot.last_summary,
            "active_capability": self.context.session.focused_capability if self.context.session is not None else None,
        }

    def model_center_payload(self) -> dict[str, Any]:
        return self.model_center.payload()

    def update_model_center(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.model_center.update(payload)

    def run_model_center_action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.model_center.run_action(action, payload)

    def plan_history_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "plan_id": task.task_id,
                "goal": task.goal,
                "steps": [
                    {
                        "capability_name": action.kind,
                        "instruction": action.instruction,
                        "status": action.status,
                        "observation": self._compact_text(action.observation),
                    }
                    for action in task.actions
                ],
            }
            for task in reversed(self._task_history[:40])
        ]

    def _result_payload(self, result: Any) -> dict[str, Any]:
        approval_request = None
        resume_token_id = None
        if result.approval_request is not None:
            approval_request = {
                "capability_name": result.approval_request.capability_name,
                "instruction": result.approval_request.instruction,
                "reason": result.approval_request.reason,
                "risk_class": result.approval_request.risk_class,
            }
        if result.resume_token is not None:
            resume_token_id = result.resume_token.token_id
        capability_name = result.action_kind
        if result.action_kind == "respond" and result.task.actions:
            last_action = result.task.actions[-1]
            if not bool(last_action.metadata.get("direct_output")):
                capability_name = "conversation"
        return {
            "status": result.status,
            "run_id": result.run_id,
            "plan_id": result.task.task_id,
            "final_answer": result.final_output,
            "capability_name": capability_name,
            "execution_trace": self._with_router_trace(
                [
                    {
                        "name": "evaluator" if bool(action.metadata.get("from_evaluator")) else action.kind,
                        "status": action.status,
                        "detail": self._compact_text(self._trace_detail_for_action(action)),
                    }
                    for action in result.task.actions
                ]
            ),
            "approval_request": approval_request,
            "resume_token_id": resume_token_id,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": str(self.workspace),
        }

    def run_history_payload(self) -> list[dict[str, Any]]:
        return list(self._run_history[:40])

    @staticmethod
    def _compact_text(value: str, *, limit: int = 200) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}...[已截断]"

    def _record_run(self, payload: dict[str, Any], *, prompt: str) -> None:
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return
        entry = {
            "run_id": run_id,
            "status": str(payload.get("status") or ""),
            "capability_name": str(payload.get("capability_name") or ""),
            "prompt": prompt,
            "final_answer_preview": str(payload.get("final_answer") or "")[:160],
        }
        self._run_inputs[run_id] = prompt
        self._run_history = [item for item in self._run_history if item.get("run_id") != run_id]
        self._run_history.insert(0, entry)
        self._run_history = self._run_history[:40]

    def _error_payload(self, exc: Exception) -> dict[str, Any]:
        error = self._normalize_error(exc)
        log_app_error(logger, error, exc=exc, event="demo_app_error")
        return {
            "status": "error",
            "final_answer": error.message,
            "capability_name": "",
            "execution_trace": self._with_router_trace(
                [
                    {
                        "name": error.stage or "run",
                        "status": "error",
                        "detail": f"{error.code}: {error.message}",
                    }
                ]
            ),
            "approval_request": None,
            "resume_token_id": None,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "error": error.as_dict(),
            "workspace": str(self.workspace),
        }

    def _normalize_error(self, exc: Exception) -> AppError:
        base_context = self._error_context()
        if isinstance(exc, AppError):
            return normalize_app_error(exc, context=base_context)
        if isinstance(exc, FileNotFoundError):
            return AppError(
                code="RESOURCE_NOT_FOUND",
                message="未找到目标资源。",
                detail=str(exc),
                stage="resolve",
                retriable=True,
                suggestion="请检查路径或文件名是否正确。",
                context=base_context,
            )
        if isinstance(exc, IsADirectoryError):
            return AppError(
                code="RESOURCE_IS_DIRECTORY",
                message="目标是目录，当前操作只接受文件。",
                detail=str(exc),
                stage="execute",
                retriable=True,
                suggestion="可以先列出目录内容，或指定目录下的某个文件。",
                context=base_context,
            )
        if isinstance(exc, NotADirectoryError):
            return AppError(
                code="RESOURCE_NOT_DIRECTORY",
                message="目标不是目录，无法执行目录操作。",
                detail=str(exc),
                stage="execute",
                retriable=True,
                suggestion="请改为读取文件，或重新指定目录。",
                context=base_context,
            )
        if isinstance(exc, ValueError) and "outside allowed roots" in str(exc):
            return AppError(
                code="RESOURCE_OUTSIDE_WORKSPACE",
                message="目标超出了当前工作区范围。",
                detail=str(exc),
                stage="resolve",
                retriable=False,
                suggestion="请只操作当前工作区内的文件或目录。",
                context=base_context,
            )
        detail = f"{type(exc).__name__}: {exc}"
        if "llm_unavailable" in detail:
            return normalize_app_error(
                exc,
                code="MODEL_UNAVAILABLE",
                message=str(exc),
                stage="conversation_response",
                retriable=False,
                suggestion="请先在前端“模型 / 配置”中为 conversation 配置可用模型。",
                context={**base_context, "exception_type": type(exc).__name__},
            )
        return normalize_app_error(
            exc,
            code="INTERNAL_ERROR",
            message="处理请求时发生了未预期错误。",
            stage="run",
            retriable=False,
            suggestion="可以重试一次；如果持续出现，请检查后端日志。",
            context={**base_context, "exception_type": type(exc).__name__},
        )

    def _error_context(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "active_agent": self._active_agent,
            "route": str((self._last_route_decision or {}).get("route") or ""),
            "route_source": str((self._last_route_decision or {}).get("source") or ""),
        }

    def _resource_payload(self, resource: Any) -> dict[str, Any]:
        return {
            "resource_id": str(getattr(resource, "resource_id", "")),
            "kind": str(getattr(resource, "kind", "")),
            "location": str(getattr(resource, "location", "")),
            "title": str(getattr(resource, "title", "")),
        }

    def _with_router_trace(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        router_step = self._router_trace_step()
        if router_step is None:
            return steps
        return [router_step, *steps]

    def _router_trace_step(self) -> dict[str, Any] | None:
        decision = self._last_route_decision
        if not decision:
            return None
        route = str(decision.get("route") or "").strip()
        source = str(decision.get("source") or "").strip()
        if not route:
            return None
        detail = f"route={route}"
        if source:
            detail = f"{detail}; source={source}"
        return {"name": "router", "status": "completed", "detail": detail}

    def _trace_detail_for_action(self, action: Any) -> str:
        base = str(action.observation or action.instruction or "")
        if not bool(action.metadata.get("from_evaluator")):
            return base
        source = str(action.metadata.get("evaluation_source") or "")
        reason = str(action.metadata.get("evaluator_reason") or "")
        detail = "decision=continue"
        if source:
            detail = f"{detail}; source={source}"
        if reason:
            detail = f"{detail}; reason={reason}"
        if base:
            detail = f"{detail}; payload={base}"
        return detail


def create_demo_assistant_app(workspace: str | Path, *, seed_config: dict[str, Any] | None = None) -> DemoAssistantApp:
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace_path}")
    model_center_store = ModelCenterStore(workspace_path / ".arf_demo_config.json")
    model_registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    model_router = ModelRouter(model_registry)
    model_center = ModelCenterService(
        store=model_center_store,
        registry=model_registry,
        router=model_router,
    )
    model_registry.register_driver(OpenAICompatibleDriver())
    model_registry.register_driver(CodexCliDriver())
    model_center.store.load_or_create(seed=seed_config)
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace_path]),
        session_memory=InMemorySessionMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace_path)},
        services={
            "model_registry": model_registry,
            "model_router": model_router,
            "sandbox": SandboxConfig(
                mode="workspace_write",
                workspace_root=workspace_path,
                writable_roots=[workspace_path],
                allow_network=False,
            ),
        },
    )
    for tool in build_default_workspace_tools():
        app_context.tools.register(tool)
    agent_registry = AgentRegistry()
    agent_registry.register_many(builtin_agent_definitions())
    context = WorkspaceContext(
        application_context=app_context,
        services={"active_agent": "workspace", "model_first_task_intent": True, "model_first_task_plan": True, "agent_registry": agent_registry},
        session=AssistantSession(session_id=str(uuid4())),
    )
    app = DemoAssistantApp(
        workspace=workspace_path,
        context=context,
        model_registry=model_registry,
        model_router=model_router,
        model_center=model_center,
        _pending_tokens={},
        _run_history=[],
        _task_history=[],
        _run_inputs={},
        _last_route_decision=None,
        _pending_workflow_clarification=None,
        _active_agent="workspace",
        _available_workspaces=[str(workspace_path)],
        agent_registry=agent_registry,
        agent_runtime=AgentRuntime(app=None),
    )
    app.agent_runtime.app = app
    app.model_center.load()
    return app
