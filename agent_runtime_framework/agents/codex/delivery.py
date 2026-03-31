from __future__ import annotations

from agent_runtime_framework.agents.codex.answer_synthesizer import build_synthesized_response_action
from agent_runtime_framework.agents.codex.models import CodexAction, CodexTask
from agent_runtime_framework.agents.codex.task_plans import has_pending_plan_task


def build_completion_guard_action(task: CodexTask) -> CodexAction | None:
    if has_pending_plan_task(task):
        return None
    if not task_requires_user_visible_summary(task):
        return None
    completed = [action for action in task.actions if action.status == "completed"]
    if not completed:
        return None
    last_action = completed[-1]
    if last_action.kind == "respond":
        return None
    return build_synthesized_response_action(task, source="completion_guard", extra_metadata={"from_completion_guard": True})


def task_requires_user_visible_summary(task: CodexTask) -> bool:
    profile = str(getattr(task, "task_profile", "") or "")
    if profile in {"change_and_verify", "multi_file_change", "debug_and_fix", "test_and_verify"}:
        return True
    return any(action.subgoal in {"modify_workspace", "verify_changes"} for action in task.actions if action.status == "completed")


def build_delivery_summary(
    task: CodexTask,
    last_action: CodexAction,
    modified_paths: list[str],
    last_observation: str,
) -> str:
    lines: list[str] = []
    lines.append(f"Completed the requested update: {describe_change_outcome(last_action, last_observation)}.")
    if modified_paths:
        lines.append(f"Files changed: {', '.join(modified_paths[:4])}.")
    else:
        lines.append("Files changed: not explicitly recorded.")
    verification_status, verification_detail = describe_verification_outcome(task, last_action)
    detail_suffix = f" ({verification_detail})" if verification_detail else ""
    lines.append(f"Verification: {verification_status}.{detail_suffix}")
    return " ".join(line.strip() for line in lines if line.strip()).strip()


def describe_change_outcome(action: CodexAction, last_observation: str) -> str:
    tool_name = str(action.metadata.get("tool_name") or "").strip()
    if tool_name == "create_workspace_path":
        return "created the requested file or directory"
    if tool_name in {"edit_workspace_text", "apply_text_patch"}:
        return "updated the requested file content"
    if tool_name == "move_workspace_path":
        return "moved the requested path"
    if tool_name == "delete_workspace_path":
        return "deleted the requested path"
    compact = " ".join(last_observation.split()).strip()
    return compact[:160].rstrip() or "completed the requested workspace change"


def describe_verification_outcome(task: CodexTask, last_action: CodexAction) -> tuple[str, str]:
    verification = getattr(task, "verification", None)
    if verification is not None:
        summary = compact_summary(str(getattr(verification, "summary", "") or ""))
        return ("passed" if bool(getattr(verification, "success", False)) else "failed", summary)
    payload_value = last_action.metadata.get("verification_result")
    if hasattr(payload_value, "success") and hasattr(payload_value, "summary"):
        summary = compact_summary(str(getattr(payload_value, "summary", "") or ""))
        return ("passed" if bool(getattr(payload_value, "success", False)) else "failed", summary)
    payload = dict(payload_value or {})
    if payload:
        summary = compact_summary(str(payload.get("summary") or ""))
        return ("passed" if bool(payload.get("success")) else "failed", summary)
    if task.state.pending_verifications:
        return ("pending", "")
    return ("not run", "")


def compact_summary(text: str, *, limit: int = 140) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
