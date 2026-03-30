from __future__ import annotations

from agent_runtime_framework.agents.codex.models import CodexTask


def synthesize_answer(task: CodexTask) -> str:
    mode = str(task.state.answer_mode or task.intent.expected_output or "").strip()
    if task.intent.task_kind == "repository_explainer":
        if mode in {"workspace_listing", "listing"}:
            return _append_references(_workspace_listing_answer(task), task)
        return _append_references(_repository_overview_answer(task), task)
    if task.intent.task_kind == "file_reader":
        return _append_references(_file_answer(task), task) if task.intent.goal_mode == "file_summary" else _file_answer(task)
    if task.intent.task_kind in {"change_and_verify", "test_and_verify"}:
        return _change_answer(task)
    return next((action.observation or "" for action in reversed(task.actions) if action.observation), task.goal)


def _workspace_listing_answer(task: CodexTask) -> str:
    lines: list[str] = []
    for item in task.state.evidence_items:
        if item.source == "list_workspace_directory" and item.content:
            return item.content
    for fact in task.state.known_facts[:3]:
        lines.append(f"- {fact}")
    return "目录结构：\n" + "\n".join(lines) if lines else "目录结构信息不足。"


def _repository_overview_answer(task: CodexTask) -> str:
    lines: list[str] = []
    structure = next((item for item in task.state.evidence_items if item.source in {"inspect_workspace_path", "list_workspace_directory"}), None)
    if structure is not None:
        lines.append(f"- 目录结构：{structure.summary or structure.content.splitlines()[0]}")
    for item in task.state.evidence_items:
        if item.source in {"read_workspace_text", "extract_workspace_outline", "rank_workspace_entries"} and item.path:
            detail = item.summary or item.content.splitlines()[0]
            lines.append(f"- {item.path} 的作用：{detail}")
    if not lines:
        return "项目概览信息不足。"
    return "根据当前收集到的证据：\n" + "\n".join(lines)


def _file_answer(task: CodexTask) -> str:
    content_item = next((item for item in reversed(task.state.evidence_items) if item.source in {"read_workspace_text", "read_workspace_excerpt", "summarize_workspace_text"}), None)
    if content_item is None:
        return "文件内容信息不足。"
    if task.intent.goal_mode == "file_summary":
        preview = [line.strip() for line in (content_item.content or content_item.summary).splitlines() if line.strip()][:4]
        bullets = "\n".join(f"- {line}" for line in preview) if preview else content_item.summary
        return f"我先基于已读取内容做一个简要说明：\n{bullets}"
    return content_item.content or content_item.summary


def _change_answer(task: CodexTask) -> str:
    modified = list(dict.fromkeys(task.memory.modified_paths))
    verification_line = "Verification: not run."
    if task.verification is not None:
        verification_line = f"Verification: {'passed' if task.verification.success else 'failed'}."
        if task.verification.summary:
            verification_line = f"Verification: {task.verification.summary}"
    else:
        last_verification = next((action.observation for action in reversed(task.actions) if action.kind == "run_verification" and (action.observation or "").strip()), "")
        if last_verification:
            verification_line = f"Verification: {last_verification}"
    lines = [f"- Completed the requested update. Files changed: {', '.join(modified)}" if modified else "- Completed the requested update."]
    latest = next((item for item in reversed(task.state.evidence_items) if item.source in {"edit_workspace_text", "apply_text_patch", "create_workspace_path"} and (item.content or item.summary)), None)
    if latest is not None:
        lines.append(f"- Latest content: {latest.content or latest.summary}")
    lines.append(f"- {verification_line}")
    return "Result:\n" + "\n".join(lines)


def _append_references(body: str, task: CodexTask) -> str:
    references: list[str] = []
    workspace_root = ""
    if getattr(task, "plan", None) is not None:
        workspace_root = str(getattr(task.plan, "metadata", {}).get("workspace_root") or "")
    for item in task.state.evidence_items:
        if item.path:
            references.append(_display_path(item.path, workspace_root))
    if getattr(task.intent, "target_ref", ""):
        references.append(str(task.intent.target_ref))
    deduped = [item for item in dict.fromkeys(ref for ref in references if ref)]
    if not deduped:
        return body
    return body + "\n引用：\n" + "\n".join(f"- {item}" for item in deduped[:6])


def _display_path(path: str, workspace_root: str) -> str:
    normalized = str(path).strip()
    if workspace_root and normalized.startswith(workspace_root):
        relative = normalized[len(workspace_root):].lstrip("/").lstrip("\\")
        return relative or "."
    return normalized
