from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.llm_access import get_application_context
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.prompting import extract_json_block
from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike


def _structured_semantic_plan(
    context: RuntimeContextLike,
    payload: dict[str, Any],
    system_prompt: str,
    max_tokens: int = 400,
) -> dict[str, Any]:
    application_context = get_application_context(context)
    if application_context is None:
        raise RuntimeError("missing application context for semantic planning")
    runtime = resolve_model_runtime(application_context, "planner")
    if runtime is None:
        raise RuntimeError("planner model unavailable for semantic planning")
    response = chat_once(
        runtime.client,
        ChatRequest(
            model=runtime.profile.model_name,
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            temperature=0.0,
            max_tokens=max_tokens,
        ),
    )
    return json.loads(extract_json_block(str(response.content or "")))


class InterpretTargetExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        payload = {
            "goal": run.goal,
            "clarification_response": run.shared_state.get("clarification_response"),
            "open_issues": run.shared_state.get("open_issues", []),
            "prior_candidates": list((run.shared_state.get("clarification_request") or {}).get("items") or []),
            "target_hints": list(node.metadata.get("target_hints") or []),
        }
        interpreted = _structured_semantic_plan(
            context,
            payload,
            (
                "Interpret the user's target request for a workspace agent. "
                "Return JSON only with keys: target_kind, preferred_path, scope_preference, "
                "exclude_paths, preferred_targets, confidence, rationale."
            ),
        )
        run.shared_state["interpreted_target"] = interpreted
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": str(interpreted.get("rationale") or "Interpreted target constraints."),
                "interpreted_target": interpreted,
                "quality_signals": [{
                    "source": "interpret_target",
                    "relevance": "high",
                    "confidence": float(interpreted.get("confidence") or 0.8),
                    "progress_contribution": "target_constraints_defined",
                    "verification_needed": False,
                    "recoverable_error": False,
                }],
                "reasoning_trace": [{"kind": "target_interpretation", "summary": str(interpreted.get("rationale") or "target constraints interpreted")}],
            },
        )


class PlanSearchExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        payload = {
            "goal": run.goal,
            "interpreted_target": dict(run.shared_state.get("interpreted_target") or {}),
            "open_issues": list(run.shared_state.get("open_issues") or []),
            "failure_history": list(run.shared_state.get("failure_history") or []),
        }
        search_plan = _structured_semantic_plan(
            context,
            payload,
            (
                "Plan a search strategy for a workspace agent. "
                "Return JSON only with keys: search_goal, semantic_queries, must_avoid, path_bias, confidence, rationale."
            ),
        )
        run.shared_state["search_plan"] = search_plan
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": str(search_plan.get("rationale") or "Prepared search plan."),
                "search_plan": search_plan,
                "quality_signals": [{
                    "source": "plan_search",
                    "relevance": "high",
                    "confidence": float(search_plan.get("confidence") or 0.8),
                    "progress_contribution": "search_strategy_defined",
                    "verification_needed": False,
                    "recoverable_error": False,
                }],
                "reasoning_trace": [{"kind": "search_plan", "summary": str(search_plan.get("rationale") or "search strategy prepared")}],
            },
        )


class PlanReadExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        payload = {
            "goal": run.goal,
            "interpreted_target": dict(run.shared_state.get("interpreted_target") or {}),
            "search_plan": dict(run.shared_state.get("search_plan") or {}),
            "open_issues": list(run.shared_state.get("open_issues") or []),
        }
        read_plan = _structured_semantic_plan(
            context,
            payload,
            (
                "Plan a file-reading strategy for a workspace agent. "
                "Return JSON only with keys: read_goal, target_path, preferred_regions, confidence, rationale."
            ),
        )
        run.shared_state["read_plan"] = read_plan
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": str(read_plan.get("rationale") or "Prepared read plan."),
                "read_plan": read_plan,
                "quality_signals": [{
                    "source": "plan_read",
                    "relevance": "high",
                    "confidence": float(read_plan.get("confidence") or 0.8),
                    "progress_contribution": "read_strategy_defined",
                    "verification_needed": False,
                    "recoverable_error": False,
                }],
                "reasoning_trace": [{"kind": "read_plan", "summary": str(read_plan.get("rationale") or "read strategy prepared")}],
            },
        )
