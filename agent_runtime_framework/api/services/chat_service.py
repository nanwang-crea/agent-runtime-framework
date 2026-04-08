from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from agent_runtime_framework.api.responses.common_payloads import with_router_trace
from agent_runtime_framework.api.responses.error_responses import ErrorResponseFactory
from agent_runtime_framework.api.responses.session_responses import SessionResponseFactory
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState
from agent_runtime_framework.memory.index import MemoryRecord
from agent_runtime_framework.resources import ResourceRef
from agent_runtime_framework.workflow import AgentGraphRuntime, GraphExecutionRuntime, RootGraphRuntime, RUN_STATUS_WAITING_INPUT, WorkflowRun, analyze_goal
from agent_runtime_framework.workflow.interaction.clarification_resolution import resolve_clarification_response
from agent_runtime_framework.workflow.planning.goal_intake import build_goal_envelope
from agent_runtime_framework.workflow.runtime.routing import RootGraphPayload, RuntimePayload
from agent_runtime_framework.workflow.runtime.factory import build_workflow_graph_execution_runtime


@dataclass(slots=True)
class ChatService:
    runtime_state: ApiRuntimeState
    session_responses: SessionResponseFactory
    error_responses: ErrorResponseFactory

    def _runtime_context(self):
        return self.runtime_state.workflow_runtime_context()

    def _graph_runtime(self) -> GraphExecutionRuntime:
        return build_workflow_graph_execution_runtime(context=self._runtime_context())

    def _agent_runtime(self) -> AgentGraphRuntime:
        return AgentGraphRuntime(
            workflow_runtime=self._graph_runtime(),
            context=self._runtime_context(),
        )

    def _register_pending_run(self, run: Any) -> str | None:
        if getattr(run, "status", None) != "waiting_approval":
            return None
        resume_token = getattr(run, "shared_state", {}).get("resume_token")
        if resume_token is None:
            return None
        token_kind = "agent_graph" if getattr(run, "metadata", {}).get("pending_subrun") is not None else "workflow"
        runtime = self._agent_runtime() if token_kind == "agent_graph" else self._graph_runtime()
        self.runtime_state._pending_tokens[resume_token.token_id] = {
            "kind": token_kind,
            "runtime": runtime,
            "run": run,
            "token": resume_token,
        }
        return resume_token.token_id

    def _interaction_payload(self, interaction: Any) -> dict[str, Any] | None:
        if interaction is None:
            return None
        if isinstance(interaction, dict):
            return {
                "kind": str(interaction.get("kind") or ""),
                "prompt": str(interaction.get("prompt") or ""),
                "summary": str(interaction.get("summary") or ""),
                "items": [str(item) for item in interaction.get("items", []) or [] if str(item).strip()],
                "source_node_id": interaction.get("source_node_id"),
                "metadata": dict(interaction.get("metadata") or {}),
            }
        return {
            "kind": str(getattr(interaction, "kind", "") or ""),
            "prompt": str(getattr(interaction, "prompt", "") or ""),
            "summary": str(getattr(interaction, "summary", "") or ""),
            "items": [str(item) for item in getattr(interaction, "items", []) or [] if str(item).strip()],
            "source_node_id": getattr(interaction, "source_node_id", None),
            "metadata": dict(getattr(interaction, "metadata", {}) or {}),
        }

    def _pending_interaction_bundle(self) -> dict[str, Any] | None:
        current = getattr(self.runtime_state, "_pending_workflow_interaction", None)
        if current is not None:
            return dict(current)
        legacy = getattr(self.runtime_state, "_pending_workflow_clarification", None)
        if legacy is None:
            return None
        return dict(legacy)

    def _assistant_text_for_run(self, run: WorkflowRun) -> str:
        pending_interaction = self._interaction_payload(getattr(run, "pending_interaction", None)) or {}
        return str(run.final_output or pending_interaction.get("prompt") or "")

    def _workflow_payload(self, run: WorkflowRun, *, resume_token_id: str | None = None) -> dict[str, Any]:
        execution_trace = [
            {"name": node.node_type, "status": run.node_states[node.node_id].status, "detail": node.node_type}
            for node in run.graph.nodes
            if node.node_id in run.node_states
        ]
        approval_request = self._workflow_approval_request(run) if run.status == "waiting_approval" else None
        pending_interaction = self._interaction_payload(getattr(run, "pending_interaction", None))
        pending_interaction_bundle = ({**dict(pending_interaction or {}), "run_id": run.run_id} if pending_interaction is not None and run.status == RUN_STATUS_WAITING_INPUT else None)
        setattr(self.runtime_state, "_pending_workflow_interaction", pending_interaction_bundle)
        if pending_interaction_bundle is not None and str(pending_interaction_bundle.get("kind") or "") == "clarification":
            setattr(self.runtime_state, "_pending_workflow_clarification", pending_interaction_bundle)
        else:
            setattr(self.runtime_state, "_pending_workflow_clarification", None)
        final_answer = str(run.final_output or (pending_interaction or {}).get("prompt") or (approval_request or {}).get("reason") or "")
        evidence = self._workflow_evidence_payload(run)
        graph_state = dict(run.metadata.get("agent_graph_state") or {})
        judge_history = list(graph_state.get("judge_history") or [])
        return {
            "status": run.status,
            "run_id": run.run_id,
            "plan_id": run.run_id,
            "final_answer": final_answer,
            "runtime": "workflow",
            "execution_trace": with_router_trace(self.runtime_state._last_route_decision, execution_trace),
            "evidence": evidence,
            "approval_request": approval_request,
            "pending_interaction": pending_interaction_bundle,
            "resume_token_id": resume_token_id,
            "session": self.session_responses.session_payload(),
            "plan_history": self.session_responses.plan_history_payload(),
            "run_history": self.session_responses.run_history_payload(),
            "memory": self.session_responses.memory_payload(),
            "context": self.session_responses.context_payload(),
            "workspace": str(self.runtime_state.workspace),
            "judge": judge_history[-1] if judge_history else None,
            "planned_subgraphs": list(graph_state.get("planned_subgraphs") or []),
            "graph_state_summary": {
                "current_iteration": graph_state.get("current_iteration", 0),
                "appended_node_ids": list(graph_state.get("appended_node_ids") or []),
            },
            "append_history": list(run.graph.metadata.get("append_history") or run.metadata.get("append_history") or []),
            "root_graph": dict(run.metadata.get("root_graph") or {}),
        }

    def _workflow_approval_request(self, run: Any) -> dict[str, Any] | None:
        resume_token = run.shared_state.get("resume_token")
        if resume_token is None:
            return None
        state = run.node_states.get(resume_token.node_id)
        node = next((candidate for candidate in run.graph.nodes if candidate.node_id == resume_token.node_id), None)
        if state is None or state.result is None:
            if node is not None and node.node_type == "delete_path":
                target = str(node.metadata.get("path") or node.node_id)
                return {
                    "capability_name": "delete_path",
                    "instruction": f"删除 {target}",
                    "reason": "删除文件需要审批",
                    "risk_class": "destructive",
                }
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

    def _workflow_evidence_payload(self, run: Any) -> dict[str, Any]:
        node_results = run.shared_state.get("node_results", {})
        aggregated = run.shared_state.get("aggregated_result")
        aggregated_output = aggregated.output if isinstance(getattr(aggregated, "output", None), dict) else {}
        synthesized = dict(run.shared_state.get("evidence_synthesis") or {})
        candidates: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        verification = dict(synthesized.get("verification") or aggregated_output.get("verification") or {})
        for result in node_results.values():
            output = result.output if isinstance(getattr(result, "output", None), dict) else {}
            candidates.extend(item for item in output.get("evidence_items", []) if isinstance(item, dict))
            chunks.extend(item for item in output.get("chunks", []) if isinstance(item, dict))
        if isinstance(aggregated_output.get("evidence_items"), list):
            candidates = [item for item in aggregated_output.get("evidence_items", []) if isinstance(item, dict)]
        if isinstance(aggregated_output.get("chunks"), list):
            chunks = [item for item in aggregated_output.get("chunks", []) if isinstance(item, dict)]
        return {"candidates": candidates, "chunks": chunks, "verification": verification}

    def _remember_workflow_run(self, message: str, run: Any) -> None:
        session = self.runtime_state.context.session
        assistant_text = self._assistant_text_for_run(run)
        if session is not None:
            session.add_turn("user", message)
            if assistant_text:
                session.add_turn("assistant", assistant_text)
            session.focused_capability = "workflow"
        pseudo_actions = []
        references: list[str] = []
        node_results = run.shared_state.get("node_results", {})
        for node in run.graph.nodes:
            state = run.node_states.get(node.node_id)
            if state is None:
                continue
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
        self.runtime_state._task_history.insert(0, workflow_task)
        self.runtime_state._task_history[:] = self.runtime_state._task_history[:40]
        if references:
            ref = ResourceRef.for_path(references[0])
            summary = str(assistant_text or f"Workflow completed for {ref.title}")
            self.runtime_state.context.application_context.session_memory.remember_focus([ref], summary=summary)
            remember = getattr(self.runtime_state.context.application_context.index_memory, "remember", None)
            if callable(remember):
                workspace = Path(self.runtime_state.workspace)
                resolved = Path(ref.location).resolve()
                path = str(resolved.relative_to(workspace)) if resolved.is_relative_to(workspace) else ref.location
                remember(MemoryRecord(key=f"focus:{path}", text=f"{path} {summary}".strip(), kind="workspace_focus", metadata={"path": path, "summary": summary}))

    def _run_conversation_branch(self, message: str, *, graph: Any, root_graph: RootGraphPayload | None = None) -> RuntimePayload:
        runtime = self._graph_runtime()
        run = WorkflowRun(goal=message, graph=graph)
        if root_graph is not None:
            run.metadata["root_graph"] = dict(root_graph)
        run.shared_state["goal_envelope"] = build_goal_envelope(
            message,
            application_context=self.runtime_state.context.application_context,
            workspace_root=self.runtime_state.workspace,
            context=self.runtime_state.context,
        ).as_payload()
        run.shared_state["memory"] = self.session_responses.memory_payload()
        run.shared_state["session_memory_snapshot"] = self.runtime_state.context.application_context.session_memory.snapshot()
        run = runtime.run(run)
        self._remember_workflow_run(message, run)
        resume_token_id = self._register_pending_run(run)
        payload = self._workflow_payload(run, resume_token_id=resume_token_id)
        self.runtime_state.record_run(payload, message)
        return payload

    def _run_agent_branch(
        self,
        message: str,
        *,
        goal_spec: Any | None = None,
        root_graph: RootGraphPayload | None = None,
        build_runtime: Callable[[], Any] | None = None,
        workflow_payload: Callable[[Any], dict[str, Any]] | None = None,
        remember_run: Callable[[str, Any], None] | None = None,
    ) -> RuntimePayload:
        runtime = build_runtime() if build_runtime is not None else self._agent_runtime()
        prior_bundle = self._pending_interaction_bundle()
        prior_run = None
        prior_state = None
        prior_graph = None
        clarification_resolution = None
        if prior_bundle and prior_bundle.get("run_id"):
            try:
                prior_run = self.runtime_state._workflow_store.load(str(prior_bundle.get("run_id")))
            except Exception:
                prior_run = None
        if prior_run is not None:
            prior_state = dict(prior_run.metadata.get("agent_graph_state") or {})
            prior_graph = prior_run.graph
            prior_goal_envelope = dict(prior_run.metadata.get("goal_envelope") or {})
            clarification_resolution = resolve_clarification_response(
                self._runtime_context(),
                prior_goal_envelope=prior_goal_envelope,
                pending_request=prior_bundle or {},
                user_response=message,
                prior_state=prior_state,
            )
            if (
                str(clarification_resolution.get("confirmed_target") or "").strip()
                and not bool(clarification_resolution.get("should_reask"))
                and "confirmed" not in clarification_resolution
            ):
                clarification_resolution = {**dict(clarification_resolution), "confirmed": True}
            updated_hints = list(dict.fromkeys([*list(prior_goal_envelope.get("target_hints") or []), *list(clarification_resolution.get("updated_target_hints") or [])]))
            prior_goal_envelope["target_hints"] = updated_hints
            memory_state = dict((prior_state or {}).get("memory_state") or {})
            semantic_memory = dict(memory_state.get("semantic_memory") or {})
            clarification_memory = dict(memory_state.get("clarification_memory") or {})
            if confirmed_target := str(clarification_resolution.get("confirmed_target") or "").strip():
                semantic_memory["confirmed_targets"] = [confirmed_target]
            if excluded_targets := [str(item) for item in clarification_resolution.get("excluded_targets", []) or [] if str(item).strip()]:
                semantic_memory["excluded_targets"] = excluded_targets
            if str(clarification_resolution.get("preferred_path") or "").strip():
                semantic_memory["interpreted_target"] = {
                    "target_kind": "file",
                    "preferred_path": str(clarification_resolution.get("preferred_path") or "").strip(),
                    "scope_preference": "any",
                    "exclude_paths": [str(item) for item in clarification_resolution.get("excluded_targets", []) or [] if str(item).strip()],
                    "confirmed": bool(clarification_resolution.get("confirmed")),
                    "confidence": float(clarification_resolution.get("confidence") or 0.8),
                }
            clarification_memory["last_resolution"] = dict(clarification_resolution)
            memory_state["semantic_memory"] = semantic_memory
            memory_state["clarification_memory"] = clarification_memory
            prior_state = {**dict(prior_state or {}), "memory_state": memory_state}
            goal_envelope = build_goal_envelope(
                str(prior_goal_envelope.get("goal") or message),
                application_context=self.runtime_state.context.application_context,
                workspace_root=self.runtime_state.workspace,
                context=self.runtime_state.context,
                goal_spec=SimpleNamespace(
                    original_goal=str(prior_goal_envelope.get("goal") or message),
                    primary_intent=str(prior_goal_envelope.get("intent") or "file_read"),
                    requires_target_interpretation=False,
                    requires_search=False,
                    requires_read=True,
                    requires_verification=False,
                    metadata={"target_hint": updated_hints[0]} if updated_hints else {},
                ),
            )
        else:
            goal_envelope = build_goal_envelope(
                message,
                application_context=self.runtime_state.context.application_context,
                workspace_root=self.runtime_state.workspace,
                context=self.runtime_state.context,
                goal_spec=goal_spec,
            )
        run = runtime.run(
            goal_envelope,
            context=self._runtime_context(),
            prior_state=prior_state,
            prior_graph=prior_graph,
            clarification_response=(message if prior_run is not None else None),
            clarification_resolution=clarification_resolution,
        )
        if root_graph is not None:
            run.metadata["root_graph"] = dict(root_graph)
        self.runtime_state._workflow_store.save(run)
        (remember_run or self._remember_workflow_run)(message, run)
        payload_builder = workflow_payload or (lambda workflow_run: self._workflow_payload(workflow_run, resume_token_id=self._register_pending_run(workflow_run)))
        payload = payload_builder(run)
        self.runtime_state.record_run(payload, message)
        payload["run_history"] = self.session_responses.run_history_payload()
        return payload

    def _root_runtime(self) -> RootGraphRuntime:
        return RootGraphRuntime(
            analyze_goal_fn=lambda message, context: analyze_goal(message, context=context),
            context=self._runtime_context(),
            mark_route_decision=lambda route, source: setattr(self.runtime_state, "_last_route_decision", {"route": route, "source": source}),
            has_pending_clarification=lambda: self._pending_interaction_bundle() is not None,
            run_conversation=lambda message, graph, root_graph: self._run_conversation_branch(message, graph=graph, root_graph=root_graph),
            run_agent=lambda message, goal, root_graph: self._run_agent_branch(message, goal_spec=goal, root_graph=root_graph),
        )

    def chat(self, message: str) -> dict[str, Any]:
        self.runtime_state.ensure_session()
        try:
            return self._root_runtime().run(message)
        except Exception as exc:
            return self.error_responses.error_payload(exc)

    def stream_chat(self, message: str) -> Iterable[dict[str, Any]]:
        yield {"type": "start", "message": message}
        self.runtime_state.ensure_session()
        yield {"type": "status", "status": {"phase": "routing", "label": "正在规划下一步动作"}}
        yield {"type": "status", "status": {"phase": "execution", "label": "正在执行工作流"}}
        payload = self.chat(message)
        if payload.get("status") == "error":
            yield {"type": "error", "error": dict(payload.get("error") or {})}
            return
        for step in payload.get("execution_trace", []):
            yield {"type": "step", "step": step}
        yield {"type": "memory", "memory": self.session_responses.memory_payload()}
        final_answer = str(payload.get("final_answer") or "")
        if not final_answer:
            yield {"type": "final", "payload": payload}
            return
        yield {"type": "delta", "delta": final_answer}
        yield {"type": "final", "payload": payload}
