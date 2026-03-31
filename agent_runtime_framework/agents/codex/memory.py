from __future__ import annotations

import re
from typing import Any

from agent_runtime_framework.agents.codex.models import TaskState


def _append_unique(items: list[str], value: str, *, max_items: int = 20) -> None:
    normalized = value.strip()
    if not normalized or normalized in items:
        return
    items.append(normalized)
    if len(items) > max_items:
        del items[0]


def _remove_value(items: list[str], value: str) -> None:
    normalized = value.strip()
    if normalized in items:
        items.remove(normalized)


def _append_typed_claim(items: list[dict[str, str]], claim: dict[str, str]) -> None:
    normalized = {key: value.strip() for key, value in claim.items() if value.strip()}
    if normalized and normalized not in items:
        items.append(normalized)


def _extract_claims(tool_name: str, text: str) -> tuple[list[str], list[dict[str, str]]]:
    claims: list[str] = []
    typed_claims: list[dict[str, str]] = []
    stripped = text.strip()
    if not stripped:
        return claims, typed_claims
    if tool_name == "inspect_workspace_path":
        first_line = stripped.splitlines()[0].strip()
        if first_line:
            claims.append(f"Directory structure: {first_line}")
            typed_claims.append({"kind": "structure", "subject": "workspace", "detail": first_line})
        for line in stripped.splitlines():
            match = re.match(r"-\s+([^:：]+)[:：](.+)", line.strip())
            if not match:
                continue
            target = match.group(1).strip()
            detail = match.group(2).strip()
            claims.append(f"{target}: {detail}")
            typed_claims.append({"kind": "role", "subject": target, "detail": detail})
    elif tool_name == "extract_workspace_outline":
        for line in stripped.splitlines():
            match = re.match(r"-\s+([^:：]+)[:：](.+)", line.strip())
            if not match:
                continue
            target = match.group(1).strip()
            detail = match.group(2).strip()
            claims.append(f"{target}: {detail}")
            typed_claims.append({"kind": "role", "subject": target, "detail": detail})
    elif tool_name in {"read_workspace_text", "read_workspace_excerpt"}:
        preview = [line.strip() for line in stripped.splitlines() if line.strip()][:2]
        for line in preview:
            claims.append(line)
            typed_claims.append({"kind": "content", "subject": "read_result", "detail": line})
    return claims, typed_claims


def update_task_memory(task: Any, action: Any, result: Any) -> None:
    state: TaskState = task.state
    tool_output = dict(getattr(result, "metadata", {}).get("tool_output") or {})
    tool_name = str(getattr(action, "metadata", {}).get("tool_name") or "")
    path = str(tool_output.get("path") or getattr(action, "metadata", {}).get("arguments", {}).get("path") or "").strip()

    if action.kind == "call_tool" and path:
        _append_unique(state.read_paths, path)
    if action.kind in {"apply_patch", "create_path", "edit_text", "move_path", "delete_path"}:
        if path:
            _append_unique(state.modified_paths, path)
        for changed_path in tool_output.get("changed_paths") or []:
            _append_unique(state.modified_paths, str(changed_path))
        _append_unique(state.pending_verifications, "verify modified workspace changes")

    summary = str(tool_output.get("summary") or getattr(result, "final_output", "")).strip()
    if summary and action.kind not in {"respond", "run_verification"}:
        _append_unique(state.known_facts, summary[:240])

    claims, typed_claims = _extract_claims(tool_name, str(tool_output.get("text") or getattr(result, "final_output", "")))
    for claim in claims:
        _append_unique(state.claims, claim)
    for claim in typed_claims:
        _append_typed_claim(state.typed_claims, claim)

    if action.kind == "run_verification":
        command = str(getattr(action, "metadata", {}).get("command") or action.instruction or "").strip()
        state.pending_verifications.clear()
        if command:
            _append_unique(state.known_facts, f"verification: {command} -> {getattr(result, 'final_output', '')}")

    if action.kind in {"call_tool", "run_verification"} and tool_name in {
        "read_workspace_text",
        "read_workspace_excerpt",
        "summarize_workspace_text",
        "inspect_workspace_path",
        "extract_workspace_outline",
        "list_workspace_directory",
    }:
        _append_unique(state.open_questions, f"answer user goal: {task.goal}")
    if action.kind == "respond":
        _remove_value(state.open_questions, f"answer user goal: {task.goal}")
        if getattr(result, "final_output", "").strip():
            _append_unique(state.claims, getattr(result, "final_output", "").strip())
