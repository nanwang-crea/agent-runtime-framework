from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4


ProcessEvent = dict[str, Any]
ProcessSink = Callable[[ProcessEvent], None]


def emit_process_event(sink: ProcessSink | None, event: ProcessEvent) -> ProcessEvent:
    normalized = normalize_process_event(event)
    if sink is not None:
        sink(dict(normalized))
    return normalized


def normalize_process_event(event: dict[str, Any]) -> ProcessEvent:
    payload = dict(event or {})
    return {
        "id": str(payload.get("id") or uuid4()),
        "kind": str(payload.get("kind") or "status"),
        "status": str(payload.get("status") or "completed"),
        "title": str(payload.get("title") or payload.get("label") or "处理中"),
        "detail": _normalize_optional_text(payload.get("detail")),
        "target": _normalize_optional_text(payload.get("target")),
        "node_id": _normalize_optional_text(payload.get("node_id")),
        "node_type": _normalize_optional_text(payload.get("node_type")),
        "metadata": dict(payload.get("metadata") or {}),
    }


def build_router_process_events(route_decision: dict[str, Any] | None, *, route: str | None = None, intent: str | None = None) -> list[ProcessEvent]:
    source = str((route_decision or {}).get("source") or "goal_analysis")
    route_name = str(route or (route_decision or {}).get("route") or "workflow")
    intent_name = str(intent or "").strip()
    detail = f"route={route_name}" + (f"; intent={intent_name}" if intent_name else "")
    return [
        normalize_process_event(
            {
                "id": "process-goal-intake",
                "kind": "plan",
                "status": "completed",
                "title": "理解请求",
                "detail": source,
                "node_id": "goal_intake",
                "node_type": "goal_intake",
            }
        ),
        normalize_process_event(
            {
                "id": "process-route-by-goal",
                "kind": "plan",
                "status": "completed",
                "title": "选择处理路径",
                "detail": detail,
                "node_id": "route_by_goal",
                "node_type": "route_by_goal",
            }
        ),
    ]


def build_process_trace_from_run(run: Any, *, route_decision: dict[str, Any] | None = None, root_graph: dict[str, Any] | None = None) -> list[ProcessEvent]:
    trace: list[ProcessEvent] = []
    route = str((root_graph or {}).get("route") or "")
    intent = str((root_graph or {}).get("intent") or "")
    trace.extend(build_router_process_events(route_decision, route=route, intent=intent))
    for node in getattr(getattr(run, "graph", None), "nodes", []) or []:
        state = getattr(run, "node_states", {}).get(node.node_id)
        if state is None:
            continue
        event = process_event_for_node(node, state.status, getattr(state, "result", None), node_id=node.node_id)
        if event is not None:
            trace.append(event)
    if getattr(run, "status", None) == "waiting_input" and getattr(run, "pending_interaction", None) is not None:
        interaction = getattr(run, "pending_interaction")
        trace.append(
            normalize_process_event(
                {
                    "kind": "approval" if str(getattr(interaction, "kind", "")) == "approval" else "status",
                    "status": "started",
                    "title": str(getattr(interaction, "summary", "") or "等待用户输入"),
                    "detail": str(getattr(interaction, "prompt", "") or "").strip() or None,
                    "node_id": getattr(interaction, "source_node_id", None),
                    "node_type": "interaction",
                }
            )
        )
    for event in build_repair_process_events(getattr(run, "shared_state", {}).get("repair_history", []) or []):
        trace.append(event)
    return dedupe_process_trace(trace)


def dedupe_process_trace(events: list[ProcessEvent]) -> list[ProcessEvent]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[ProcessEvent] = []
    for event in events:
        key = (
            str(event.get("kind") or ""),
            str(event.get("status") or ""),
            str(event.get("node_id") or ""),
            str(event.get("title") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def build_repair_process_events(repair_history: list[Any]) -> list[ProcessEvent]:
    events: list[ProcessEvent] = []
    for index, record in enumerate(repair_history, start=1):
        if not isinstance(record, dict):
            continue
        contract_kind = str(record.get("contract_kind") or "").strip() or "contract"
        success = bool(record.get("success"))
        attempts_used = int(record.get("attempts_used") or 0)
        title = "内部修复输出" if success else "内部修复未完成"
        detail_parts = [contract_kind]
        if attempts_used:
            detail_parts.append(f"{attempts_used} 次尝试")
        final_error = str(record.get("final_error") or "").strip()
        if final_error and not success:
            detail_parts.append(final_error)
        events.append(
            normalize_process_event(
                {
                    "id": str(record.get("id") or f"repair-{index}-{contract_kind}"),
                    "kind": "plan",
                    "status": "completed" if success else "started",
                    "title": title,
                    "detail": " · ".join(detail_parts),
                    "node_type": "repair",
                    "metadata": {
                        "repair": True,
                        "contract_kind": contract_kind,
                        "attempts_used": attempts_used,
                        "initial_error": str(record.get("initial_error") or ""),
                        "final_error": final_error,
                    },
                }
            )
        )
    return events


def process_event_for_node(node: Any, status: str, result: Any = None, *, node_id: str | None = None) -> ProcessEvent | None:
    node_type = str(getattr(node, "node_type", "") or "")
    metadata = dict(getattr(node, "metadata", {}) or {})
    output = dict(getattr(result, "output", {}) or {}) if getattr(result, "output", None) is not None else {}
    references = [str(item) for item in getattr(result, "references", []) or [] if str(item).strip()]
    title, kind = _event_title_and_kind(node_type, metadata, output)
    detail = _event_detail(node_type, metadata, output, result)
    target = _event_target(node_type, metadata, output, references)
    normalized_status = _normalize_process_status(status)
    if not title:
        return None
    return normalize_process_event(
        {
            "kind": kind,
            "status": normalized_status,
            "title": title,
            "detail": detail,
            "target": target,
            "node_id": str(node_id or getattr(node, "node_id", "") or ""),
            "node_type": node_type,
            "metadata": _event_metadata(node_type, metadata, output, references),
        }
    )


def _event_title_and_kind(node_type: str, metadata: dict[str, Any], output: dict[str, Any]) -> tuple[str, str]:
    mapping: dict[str, tuple[str, str]] = {
        "goal_intake": ("理解请求", "plan"),
        "context_assembly": ("整理上下文", "plan"),
        "plan": ("规划下一步", "plan"),
        "interpret_target": ("理解目标文件", "plan"),
        "target_resolution": ("定位目标", "search"),
        "plan_search": ("制定搜索策略", "plan"),
        "workspace_discovery": ("搜索工作区", "search"),
        "content_search": ("搜索内容", "search"),
        "plan_read": ("制定阅读计划", "plan"),
        "chunked_file_read": ("读取文件", "read"),
        "tool_call": ("调用工具", "exec"),
        "create_path": ("创建文件", "edit"),
        "move_path": ("移动路径", "edit"),
        "delete_path": ("删除路径", "edit"),
        "apply_patch": ("应用补丁", "edit"),
        "write_file": ("写入文件", "edit"),
        "append_text": ("追加内容", "edit"),
        "verification": ("验证变更", "test"),
        "verification_step": ("验证结果", "test"),
        "aggregate_results": ("汇总结果", "status"),
        "evidence_synthesis": ("整理证据", "status"),
        "judge": ("评估进展", "plan"),
        "approval_gate": ("等待审批", "approval"),
        "clarification": ("请求补充信息", "approval"),
        "conversation_response": ("生成回答", "reply"),
        "final_response": ("生成最终答复", "reply"),
    }
    if node_type in mapping:
        return mapping[node_type]
    if node_type.startswith("plan_"):
        return ("规划下一步", "plan")
    if node_type.startswith("judge_"):
        return ("评估进展", "plan")
    return (node_type.replace("_", " ").strip() or "处理中", "status")


def _event_target(node_type: str, metadata: dict[str, Any], output: dict[str, Any], references: list[str]) -> str | None:
    for key in ("path", "target_path", "preferred_path", "destination_path"):
        value = str(metadata.get(key) or output.get(key) or "").strip()
        if value:
            return value
    tool_output = dict(output.get("tool_output") or {})
    for key in ("path", "resolved_path", "destination_path"):
        value = str(tool_output.get(key) or "").strip()
        if value:
            return value
    if references:
        return references[0]
    return None


def _event_detail(node_type: str, metadata: dict[str, Any], output: dict[str, Any], result: Any) -> str | None:
    for key in ("summary", "reason", "instruction", "rationale", "search_goal", "read_goal"):
        value = str(output.get(key) or metadata.get(key) or "").strip()
        if value:
            return value
    if node_type == "tool_call":
        tool_name = str(output.get("tool_name") or metadata.get("tool_name") or "").strip()
        if tool_name:
            return tool_name
    error = str(getattr(result, "error", "") or "").strip() if result is not None else ""
    return error or None


def _event_metadata(node_type: str, metadata: dict[str, Any], output: dict[str, Any], references: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    tool_output = dict(output.get("tool_output") or {})
    arguments = dict(output.get("arguments") or {})

    if node_type == "chunked_file_read":
        payload["files"] = [str(output.get("path") or references[0] if references else "").strip()] if (output.get("path") or references) else []
        payload["chunk_count"] = len(output.get("chunks") or [])
        payload["read_mode"] = str((output.get("artifacts") or {}).get("read_mode") or "").strip()
    elif node_type in {"content_search", "workspace_discovery"}:
        payload["query"] = ", ".join(str(item) for item in ((output.get("artifacts") or {}).get("search_terms") or []) if str(item).strip())
        payload["match_count"] = len(output.get("matches") or output.get("candidates") or [])
        if node_type == "workspace_discovery":
            sample = [str(item) for item in ((output.get("artifacts") or {}).get("tree_sample") or [])[:6] if str(item).strip()]
            payload["files"] = sample
    elif node_type in {"create_path", "move_path", "delete_path", "apply_patch", "write_file", "append_text"}:
        changed_paths = [str(item).strip() for item in (tool_output.get("changed_paths") or []) if str(item).strip()]
        base_paths = [
            str(tool_output.get("path") or "").strip(),
            str(tool_output.get("resolved_path") or "").strip(),
            str(tool_output.get("destination_path") or "").strip(),
            str(arguments.get("path") or "").strip(),
            str(arguments.get("destination_path") or "").strip(),
        ]
        payload["changed_paths"] = [path for path in [*changed_paths, *base_paths] if path]
    elif node_type == "tool_call":
        payload["tool_name"] = str(output.get("tool_name") or metadata.get("tool_name") or "").strip()
        payload["command"] = str(arguments.get("command") or tool_output.get("command") or "").strip()
        if path := str(arguments.get("path") or tool_output.get("path") or "").strip():
            payload["files"] = [path]
    elif node_type in {"verification", "verification_step"}:
        payload["verification_events"] = len(output.get("verification_events") or [])
    elif node_type in {"plan_search", "plan_read", "interpret_target", "target_resolution"}:
        if path := str(output.get("target_path") or output.get("preferred_path") or metadata.get("path") or "").strip():
            payload["files"] = [path]
        if queries := [str(item).strip() for item in (output.get("semantic_queries") or []) if str(item).strip()]:
            payload["query"] = ", ".join(queries)

    if not payload.get("files") and references:
        payload["files"] = references[:6]
    return payload


def _normalize_process_status(status: str) -> str:
    text = str(status or "").strip().lower()
    if text in {"running", "started", "in_progress"}:
        return "started"
    if text in {"failed", "error"}:
        return "error"
    if text in {"waiting_approval", "waiting_input"}:
        return "started"
    return "completed"


def _normalize_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
