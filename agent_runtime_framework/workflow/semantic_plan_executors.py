from __future__ import annotations

import json
from typing import Any

from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.contract_repair import repair_structured_output
from agent_runtime_framework.workflow.llm_access import get_application_context
from agent_runtime_framework.workflow.memory_updates import remember_clarification, remember_semantic_plan
from agent_runtime_framework.workflow.memory_views import build_semantic_memory_view
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.prompting import extract_json_block
from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _as_float(value: Any, default: float = 0.8) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


def _normalize_interpreted_target(payload: dict[str, Any], *, fallback_hint: str = "") -> dict[str, Any]:
    preferred_path = str(payload.get("preferred_path") or "").strip()
    scope_preference = str(payload.get("scope_preference") or "").strip()
    if not preferred_path:
        raise ValueError("semantic target interpretation missing preferred_path")
    if not scope_preference:
        raise ValueError("semantic target interpretation missing scope_preference")
    if "confirmed" not in payload:
        raise ValueError("semantic target interpretation missing confirmed")
    normalized = {
        "target_kind": str(payload.get("target_kind") or "").strip() or "unknown",
        "preferred_path": preferred_path,
        "scope_preference": scope_preference,
        "exclude_paths": _as_string_list(payload.get("exclude_paths")),
        "confirmed": bool(payload.get("confirmed")),
        "confidence": _as_float(payload.get("confidence"), 0.8),
        "rationale": str(payload.get("rationale") or "Interpreted target constraints.").strip() or "Interpreted target constraints.",
    }
    return normalized


def _normalize_search_plan(payload: dict[str, Any], *, interpreted_target: dict[str, Any]) -> dict[str, Any]:
    search_goal = str(payload.get("search_goal") or "").strip()
    queries = _as_string_list(payload.get("semantic_queries"))
    if not search_goal:
        raise ValueError("semantic search planning missing search_goal")
    if not queries:
        raise ValueError("semantic search planning missing semantic_queries")
    normalized = {
        "search_goal": search_goal,
        "semantic_queries": queries,
        "must_avoid": _as_string_list(payload.get("must_avoid")),
        "path_bias": _as_string_list(payload.get("path_bias")),
        "confidence": _as_float(payload.get("confidence"), 0.8),
        "rationale": str(payload.get("rationale") or "Prepared search plan.").strip() or "Prepared search plan.",
    }
    return normalized


def _normalize_read_plan(payload: dict[str, Any], *, interpreted_target: dict[str, Any], search_plan: dict[str, Any]) -> dict[str, Any]:
    read_goal = str(payload.get("read_goal") or "").strip()
    target_path = str(payload.get("target_path") or "").strip()
    if not read_goal:
        raise ValueError("semantic read planning missing read_goal")
    if not target_path:
        raise ValueError("semantic read planning missing target_path")
    preferred_regions = _as_string_list(payload.get("preferred_regions"))
    if not preferred_regions:
        normalized_target = target_path.lower()
        if normalized_target.endswith(("readme.md", ".md", ".rst", ".txt")):
            preferred_regions = ["head"]
        else:
            preferred_regions = ["head", "tail"]
    normalized = {
        "read_goal": read_goal,
        "target_path": target_path,
        "preferred_regions": preferred_regions,
        "confidence": _as_float(payload.get("confidence"), 0.8),
        "rationale": str(payload.get("rationale") or "Prepared read plan.").strip() or "Prepared read plan.",
    }
    return normalized


def _shared_memory_state(run: WorkflowRun) -> dict[str, Any]:
    memory_state = dict(run.shared_state.get("memory_state") or {})
    memory_state.setdefault("clarification_memory", {})
    memory_state.setdefault("semantic_memory", {})
    memory_state.setdefault("execution_memory", {})
    memory_state.setdefault("preference_memory", {})
    return memory_state


def _normalize_with_repair(
    context: RuntimeContextLike,
    *,
    role: str,
    contract_kind: str,
    required_fields: list[str],
    request_payload: dict[str, Any],
    original_output: dict[str, Any],
    normalizer,
    extra_instructions: str = "",
    **normalizer_kwargs: Any,
) -> dict[str, Any]:
    try:
        return normalizer(original_output, **normalizer_kwargs)
    except ValueError as exc:
        repaired = repair_structured_output(
            context,
            role=role,
            contract_kind=contract_kind,
            required_fields=required_fields,
            original_output=original_output,
            validation_error=str(exc),
            request_payload=request_payload,
            extra_instructions=extra_instructions,
        )
        if not isinstance(repaired, dict):
            raise
        return normalizer(repaired, **normalizer_kwargs)


class InterpretTargetExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        state = run.shared_state.get("agent_graph_state_ref")
        semantic_view = build_semantic_memory_view(state) if state is not None else {}
        payload = {
            "goal": run.goal,
            "clarification_response": run.shared_state.get("clarification_response"),
            "open_issues": run.shared_state.get("open_issues", []),
            "prior_candidates": list((run.shared_state.get("clarification_request") or {}).get("items") or []),
            "target_hints": list(node.metadata.get("target_hints") or []),
            "failure_history": list(run.shared_state.get("failure_history") or []),
            "memory_view": semantic_view,
        }
        raw_interpreted = _structured_semantic_plan(
            context,
            payload,
            (
                "Interpret the user's target request for a workspace agent. "
                "Use clarification_response and prior_candidates when present. "
                "Return JSON only with keys: target_kind, preferred_path, scope_preference, "
                "exclude_paths, confirmed, confidence, rationale."
            ),
        )
        interpreted = _normalize_with_repair(
            context,
            role="planner",
            contract_kind="interpreted_target",
            required_fields=["preferred_path", "scope_preference", "confirmed"],
            request_payload=payload,
            original_output=raw_interpreted,
            normalizer=_normalize_interpreted_target,
            extra_instructions="preferred_path must be concrete and scope_preference must be non-empty.",
            fallback_hint=str((node.metadata.get("target_hints") or [""])[0] or ""),
        )
        run.shared_state["interpreted_target"] = interpreted
        if state is not None:
            remember_semantic_plan(state, interpreted_target=interpreted)
            remember_clarification(
                state,
                candidate_items=payload["prior_candidates"],
                last_resolution={"preferred_path": interpreted["preferred_path"], "confidence": interpreted["confidence"]},
            )
            run.shared_state["memory_state"] = state.memory_state.as_payload()
        else:
            memory_state = _shared_memory_state(run)
            memory_state["semantic_memory"]["interpreted_target"] = dict(interpreted)
            if payload["prior_candidates"]:
                memory_state["clarification_memory"]["candidate_items"] = list(payload["prior_candidates"])
            memory_state["clarification_memory"]["last_resolution"] = {"preferred_path": interpreted["preferred_path"], "confidence": interpreted["confidence"]}
            run.shared_state["memory_state"] = memory_state
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
        state = run.shared_state.get("agent_graph_state_ref")
        semantic_view = build_semantic_memory_view(state) if state is not None else {}
        interpreted_target = dict(run.shared_state.get("interpreted_target") or {})
        payload = {
            "goal": run.goal,
            "interpreted_target": interpreted_target,
            "open_issues": list(run.shared_state.get("open_issues") or []),
            "failure_history": list(run.shared_state.get("failure_history") or []),
            "attempted_strategies": list(run.shared_state.get("attempted_strategies") or []),
            "memory_view": semantic_view,
        }
        raw_search_plan = _structured_semantic_plan(
            context,
            payload,
            (
                "Plan a search strategy for a workspace agent. "
                "Use interpreted_target and failure_history to avoid repeating ineffective search behavior. "
                "Return JSON only with keys: search_goal, semantic_queries, must_avoid, path_bias, confidence, rationale."
            ),
        )
        search_plan = _normalize_with_repair(
            context,
            role="planner",
            contract_kind="search_plan",
            required_fields=["search_goal", "semantic_queries"],
            request_payload=payload,
            original_output=raw_search_plan,
            normalizer=_normalize_search_plan,
            extra_instructions="semantic_queries must be a non-empty array of strings.",
            interpreted_target=interpreted_target,
        )
        run.shared_state["search_plan"] = search_plan
        if state is not None:
            remember_semantic_plan(state, search_plan=search_plan)
            run.shared_state["memory_state"] = state.memory_state.as_payload()
        else:
            memory_state = _shared_memory_state(run)
            memory_state["semantic_memory"]["search_plan"] = dict(search_plan)
            run.shared_state["memory_state"] = memory_state
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
        state = run.shared_state.get("agent_graph_state_ref")
        semantic_view = build_semantic_memory_view(state) if state is not None else {}
        interpreted_target = dict(run.shared_state.get("interpreted_target") or {})
        search_plan = dict(run.shared_state.get("search_plan") or {})
        payload = {
            "goal": run.goal,
            "interpreted_target": interpreted_target,
            "search_plan": search_plan,
            "open_issues": list(run.shared_state.get("open_issues") or []),
            "failure_history": list(run.shared_state.get("failure_history") or []),
            "memory_view": semantic_view,
        }
        raw_read_plan = _structured_semantic_plan(
            context,
            payload,
            (
                "Plan a file-reading strategy for a workspace agent. "
                "Use interpreted_target and search_plan to choose the exact file and the most relevant regions. "
                "Return JSON only with keys: read_goal, target_path, preferred_regions, confidence, rationale. "
                "preferred_regions must be a non-empty array using values such as head or tail. "
                "For README or overview documents, default preferred_regions to [\"head\"]."
            ),
        )
        read_plan = _normalize_with_repair(
            context,
            role="planner",
            contract_kind="read_plan",
            required_fields=["read_goal", "target_path", "preferred_regions"],
            request_payload=payload,
            original_output=raw_read_plan,
            normalizer=_normalize_read_plan,
            extra_instructions="preferred_regions must be a non-empty array; for README or overview documents use [\"head\"].",
            interpreted_target=interpreted_target,
            search_plan=search_plan,
        )
        run.shared_state["read_plan"] = read_plan
        if state is not None:
            remember_semantic_plan(state, read_plan=read_plan)
            run.shared_state["memory_state"] = state.memory_state.as_payload()
        else:
            memory_state = _shared_memory_state(run)
            memory_state["semantic_memory"]["read_plan"] = dict(read_plan)
            run.shared_state["memory_state"] = memory_state
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
