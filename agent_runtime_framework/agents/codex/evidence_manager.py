from __future__ import annotations

from agent_runtime_framework.agents.codex.models import CodexTask, EvidenceItem
from agent_runtime_framework.agents.codex.state import append_evidence_item, sync_task_state_from_memory


def record_action_evidence(task: CodexTask, action: object, result: object) -> None:
    tool_name = str(getattr(action, "metadata", {}).get("tool_name") or getattr(action, "kind", "")).strip()
    tool_output = dict(getattr(result, "metadata", {}).get("tool_output") or {})
    summary = str(tool_output.get("summary") or getattr(result, "final_output", "") or "").strip()
    path = str(tool_output.get("path") or getattr(action, "metadata", {}).get("arguments", {}).get("path") or "").strip()
    kind = str(tool_output.get("resource_kind") or tool_name or "observation")
    content = str(tool_output.get("text") or getattr(result, "final_output", "") or "").strip()
    if summary or content:
        append_evidence_item(
            task,
            EvidenceItem(
                source=tool_name or "action",
                kind=kind,
                summary=summary[:240],
                path=path,
                content=content,
                relevance=0.8 if summary else 0.5,
            ),
        )
    _sync_state_from_evidence(task, path=path)
    sync_task_state_from_memory(task)


def evidence_gap(task: CodexTask) -> list[str]:
    intent = task.intent
    state = task.state
    evidence_sources = {item.source for item in state.evidence_items}
    missing: list[str] = []
    if intent.task_kind == "repository_explainer":
        if "list_workspace_directory" not in evidence_sources and "inspect_workspace_path" not in evidence_sources:
            missing.append("structure")
        if not any(source in evidence_sources for source in {"read_workspace_text", "extract_workspace_outline", "rank_workspace_entries"}):
            missing.append("representative_files")
    if intent.task_kind == "file_reader":
        if not any(source in evidence_sources for source in {"read_workspace_text", "read_workspace_excerpt", "summarize_workspace_text"}):
            missing.append("file_content")
    if intent.task_kind in {"change_and_verify", "test_and_verify"} and getattr(task.memory, "pending_verifications", []):
        missing.append("verification")
    return missing


def _sync_state_from_evidence(task: CodexTask, *, path: str) -> None:
    if path and not str(getattr(task.state, "resolved_target", "") or "").strip():
        task.state.resolved_target = path
    task.state.pending_actions = evidence_gap(task)
