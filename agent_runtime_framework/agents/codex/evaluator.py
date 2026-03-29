from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction, CodexEvaluationDecision, CodexTask
from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import build_codex_system_prompt, extract_json_block, render_codex_prompt_doc
from agent_runtime_framework.agents.codex.run_context import build_run_context_block
from agent_runtime_framework.agents.codex.workflows import workflow_name_for_task_profile
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime

logger = logging.getLogger(__name__)


def evaluate_codex_output(task: CodexTask, session: Any, context: Any, tool_names: list[str]) -> CodexEvaluationDecision:
    if task.memory.pending_verifications:
        command = task.memory.pending_verifications[0]
        return CodexEvaluationDecision(
            status="continue",
            next_action=CodexAction(
                kind="run_verification",
                instruction=command,
                subgoal="verify_changes",
                metadata={"command": command, "from_evaluator": True, "evaluator_reason": "pending_verification"},
            ),
            summary="verification required before finish",
        )
    if task.memory.open_questions and _last_completed_action(task) and _last_completed_action(task).kind == "respond":
        return CodexEvaluationDecision(status="continue", summary="cannot finish while open questions remain")
    synthesized_reply = _build_missing_final_respond(task)
    if synthesized_reply is not None:
        return CodexEvaluationDecision(
            status="continue",
            next_action=CodexAction(
                kind="respond",
                instruction=synthesized_reply,
                subgoal="synthesize_answer",
                metadata={"direct_output": True, "from_evaluator": True, "evaluator_reason": "missing_final_summary"},
            ),
            summary="task needs final user-visible summary before finish",
        )
    llm_decision = _evaluate_with_model(task, session, context, tool_names)
    if llm_decision.status != "abstain":
        if llm_decision.next_action is not None:
            llm_decision.next_action.metadata["evaluation_source"] = "model"
        return llm_decision
    fallback = _evaluate_deterministically(task, session, context, tool_names)
    if fallback.next_action is not None:
        fallback.next_action.metadata["evaluation_source"] = "fallback"
    return fallback


def _evaluate_with_model(task: CodexTask, session: Any, context: Any, tool_names: list[str]) -> CodexEvaluationDecision:
    runtime = resolve_model_runtime(context.application_context, "evaluator")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None:
        return CodexEvaluationDecision()
    completed = [action for action in task.actions if action.status == "completed"]
    if not completed:
        return CodexEvaluationDecision()
    action_lines = [
        f"- kind: {action.kind}; instruction: {action.instruction}; observation: {action.observation or ''}; tool_name: {action.metadata.get('tool_name', '')}"
        for action in completed[-8:]
    ]
    tool_list = ", ".join(tool_names)
    persona = resolve_runtime_persona(context, task=task)
    workflow_name = workflow_name_for_task_profile(task.task_profile)
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=build_codex_system_prompt(
                            render_codex_prompt_doc("evaluator_system", workflow_name=workflow_name or "general"),
                            workflow_name=workflow_name,
                            persona=persona,
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=render_codex_prompt_doc(
                            "evaluator_user",
                            goal=task.goal,
                            workflow_name=workflow_name or "(none)",
                            run_context_block=build_run_context_block(context, task=task, session=session, user_input=task.goal, persona=persona),
                            progress_summary=_build_evaluator_progress_summary(task),
                            recent_completed_actions=chr(10).join(action_lines),
                            available_tools=tool_list,
                            evidence_threshold=persona.evidence_threshold,
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=700,
            ),
        )
    except Exception as exc:
        logger.warning("evaluator request failed: %s: %s", type(exc).__name__, exc)
        return CodexEvaluationDecision()
    raw_content = (response.content or "").strip()
    try:
        parsed = json.loads(extract_json_block(raw_content))
    except Exception:
        logger.warning("evaluator invalid json: raw=%s", raw_content[:400])
        return CodexEvaluationDecision()
    normalized = _normalize_evaluator_decision(parsed, tool_names=set(tool_names))
    if normalized.status == "abstain":
        logger.warning("evaluator normalization failed: parsed=%s", json.dumps(parsed, ensure_ascii=False)[:400])
    return normalized


def _evaluate_deterministically(task: CodexTask, _session: Any, _context: Any, tool_names: list[str]) -> CodexEvaluationDecision:
    completed = [action for action in task.actions if action.status == "completed"]
    if not completed:
        return CodexEvaluationDecision()
    last_action = completed[-1]
    if last_action.kind == "respond" and not task.memory.open_questions and not task.memory.pending_verifications:
        return CodexEvaluationDecision(status="finish")
    if bool(last_action.metadata.get("from_evaluator")) and last_action.kind == "respond":
        return CodexEvaluationDecision()
    tool_name = str(last_action.metadata.get("tool_name") or "").strip()
    arguments = dict(last_action.metadata.get("arguments") or {})
    target_semantics = _extract_target_semantics(task, last_action)
    if (
        task.task_profile == "file_reader"
        and target_semantics is not None
        and target_semantics.get("resource_kind") == "file"
        and tool_name == "resolve_workspace_target"
    ):
        follow_up_tool = "summarize_workspace_text" if _goal_prefers_summary(task.goal) and "summarize_workspace_text" in tool_names else "read_workspace_text"
        if follow_up_tool in tool_names:
            follow_up_path = _relative_target_path(target_semantics, _context)
            return CodexEvaluationDecision(
                status="continue",
                next_action=CodexAction(
                    kind="call_tool",
                    instruction=task.goal,
                    subgoal="gather_evidence",
                    metadata={
                        "tool_name": follow_up_tool,
                        "arguments": {"path": follow_up_path},
                        "from_evaluator": True,
                        "evaluator_reason": "file_evidence_insufficient",
                    },
                ),
                summary="resolved file target still needs content evidence",
            )
    if (
        task.task_profile == "repository_explainer"
        and target_semantics is not None
        and target_semantics.get("resource_kind") == "directory"
        and "inspect_workspace_path" in tool_names
        and tool_name != "inspect_workspace_path"
        and not _has_repository_structure_evidence(task)
    ):
        inspect_path = str(arguments.get("path") or target_semantics.get("path") or "").strip()
        return CodexEvaluationDecision(
            status="continue",
            next_action=CodexAction(
                kind="call_tool",
                instruction=task.goal,
                subgoal="gather_evidence",
                metadata={
                    "tool_name": "inspect_workspace_path",
                    "arguments": {
                        "path": inspect_path,
                        "use_last_focus": True,
                    },
                    "from_evaluator": True,
                    "evaluator_reason": "directory_evidence_insufficient",
                },
            ),
            summary="directory evidence needs deeper inspection",
        )
    if (
        task.task_profile == "repository_explainer"
        and tool_name == "inspect_workspace_path"
        and "rank_workspace_entries" in tool_names
        and not _has_completed_tool(task, "rank_workspace_entries")
    ):
        inspect_path = str(arguments.get("path") or (target_semantics.get("path") if target_semantics else "")).strip()
        return CodexEvaluationDecision(
            status="continue",
            next_action=CodexAction(
                kind="call_tool",
                instruction=task.goal,
                subgoal="gather_evidence",
                metadata={
                    "tool_name": "rank_workspace_entries",
                    "arguments": {"path": inspect_path, "query": task.goal},
                    "from_evaluator": True,
                    "evaluator_reason": "representative_files_needed",
                },
            ),
            summary="need representative files before repository summary",
        )
    next_outline_path = _next_ranked_outline_path(task)
    if (
        task.task_profile == "repository_explainer"
        and next_outline_path
        and "extract_workspace_outline" in tool_names
        and tool_name in {"rank_workspace_entries", "extract_workspace_outline"}
    ):
        return CodexEvaluationDecision(
            status="continue",
            next_action=CodexAction(
                kind="call_tool",
                instruction=task.goal,
                subgoal="gather_evidence",
                metadata={
                    "tool_name": "extract_workspace_outline",
                    "arguments": {"path": next_outline_path},
                    "from_evaluator": True,
                    "evaluator_reason": "representative_outline_needed",
                },
            ),
            summary="need representative file outline before repository summary",
        )
    if tool_name in {"read_workspace_text", "read_workspace_excerpt", "summarize_workspace_text", "inspect_workspace_path", "list_workspace_directory", "extract_workspace_outline"}:
        synthesized = _synthesize_knowledge_answer(task, completed)
        if synthesized:
            return CodexEvaluationDecision(
                status="continue",
                next_action=CodexAction(
                    kind="respond",
                    instruction=synthesized,
                    subgoal="synthesize_answer",
                    metadata={"direct_output": True, "from_evaluator": True, "evaluator_reason": "synthesize_answer"},
                ),
                summary="raw evidence should be synthesized before finishing",
            )
    return CodexEvaluationDecision()


def _build_missing_final_respond(task: CodexTask) -> str | None:
    if not _task_requires_final_summary(task):
        return None
    completed = [action for action in task.actions if action.status == "completed"]
    if not completed:
        return None
    last_action = completed[-1]
    if last_action.kind == "respond":
        return None
    modified_paths = list(dict.fromkeys(path for path in task.memory.modified_paths if str(path).strip()))
    verification_pending = bool(task.memory.pending_verifications)
    last_observation = str(last_action.observation or "").strip()
    verification_status, verification_detail = _describe_verification_outcome(task, last_action)
    change_detail = _describe_change_outcome(last_action, last_observation)
    lines: list[str] = []
    if change_detail:
        lines.append(f"Completed the requested update: {change_detail}.")
    else:
        lines.append("Completed the requested workspace update.")
    if modified_paths:
        lines.append(f"Files changed: {', '.join(modified_paths[:4])}.")
    else:
        lines.append("Files changed: not explicitly recorded.")
    if verification_pending:
        lines.append("Verification: pending.")
    elif verification_status:
        detail_suffix = f" ({verification_detail})" if verification_detail else ""
        lines.append(f"Verification: {verification_status}.{detail_suffix}")
    else:
        lines.append("Verification: not run.")
    return " ".join(line.strip() for line in lines if line.strip()).strip()


def _task_requires_final_summary(task: CodexTask) -> bool:
    profile = str(getattr(task, "task_profile", "") or "")
    if profile in {"change_and_verify", "multi_file_change", "debug_and_fix", "test_and_verify"}:
        return True
    completed = [action for action in task.actions if action.status == "completed"]
    if not completed:
        return False
    return any(action.subgoal in {"modify_workspace", "verify_changes"} for action in completed)


def _describe_change_outcome(action: CodexAction, last_observation: str) -> str:
    tool_name = str(action.metadata.get("tool_name") or "").strip()
    if tool_name == "create_workspace_path":
        return "created the requested file or directory"
    if tool_name in {"edit_workspace_text", "apply_text_patch"}:
        return "updated the requested file content"
    if tool_name == "move_workspace_path":
        return "moved the requested path"
    if tool_name == "delete_workspace_path":
        return "deleted the requested path"
    compact_observation = " ".join(last_observation.split())[:160].strip()
    return compact_observation or "applied the requested workspace change"


def _describe_verification_outcome(task: CodexTask, last_action: CodexAction) -> tuple[str, str]:
    verification_result = getattr(task, "verification", None)
    if verification_result is not None:
        success = bool(getattr(verification_result, "success", False))
        summary = str(getattr(verification_result, "summary", "") or "").strip()
        return ("passed" if success else "failed", _compact_summary(summary))
    verification_payload = dict(last_action.metadata.get("verification_result") or {})
    if verification_payload:
        success = bool(verification_payload.get("success"))
        summary = str(verification_payload.get("summary") or "").strip()
        return ("passed" if success else "failed", _compact_summary(summary))
    return ("", "")


def _compact_summary(text: str, *, limit: int = 140) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _last_completed_action(task: CodexTask) -> CodexAction | None:
    for action in reversed(task.actions):
        if action.status == "completed":
            return action
    return None


def _has_completed_tool(task: CodexTask, tool_name: str) -> bool:
    return any(
        action.status == "completed" and str(action.metadata.get("tool_name") or "") == tool_name
        for action in task.actions
    )


def _next_ranked_outline_path(task: CodexTask) -> str:
    ranked_paths: list[str] = []
    outlined_paths: set[str] = set()
    for action in task.actions:
        if action.status != "completed":
            continue
        tool_name = str(action.metadata.get("tool_name") or "")
        if tool_name == "rank_workspace_entries":
            result = dict(action.metadata.get("result") or {})
            tool_output = dict(result.get("tool_output") or {})
            ranked_paths = [str(item).strip() for item in tool_output.get("ranked_paths") or [] if str(item).strip()]
        elif tool_name == "extract_workspace_outline":
            path = str(dict(action.metadata.get("arguments") or {}).get("path") or "").strip()
            if path:
                outlined_paths.add(path)
    for path in ranked_paths:
        if path not in outlined_paths:
            return path
    return ""


def _normalize_evaluator_decision(parsed: dict[str, Any], *, tool_names: set[str]) -> CodexEvaluationDecision:
    if not isinstance(parsed, dict):
        return CodexEvaluationDecision()
    decision = str(parsed.get("decision") or "").strip().lower()
    if decision == "finish":
        return CodexEvaluationDecision(status="finish")
    if decision != "continue":
        return CodexEvaluationDecision()
    kind = str(parsed.get("kind") or "").strip()
    if kind not in {"call_tool", "respond"}:
        return CodexEvaluationDecision()
    instruction = str(parsed.get("instruction") or "").strip()
    tool_name = str(parsed.get("tool_name") or "").strip()
    arguments = dict(parsed.get("arguments") or {})
    if kind == "call_tool":
        if not tool_name or tool_name not in tool_names:
            return CodexEvaluationDecision()
        action = CodexAction(
            kind="call_tool",
            instruction=instruction or tool_name,
            subgoal="gather_evidence",
            metadata={"tool_name": tool_name, "arguments": arguments, "from_evaluator": True, "evaluator_reason": "llm_continue"},
        )
        return CodexEvaluationDecision(status="continue", next_action=action)
    if not instruction:
        return CodexEvaluationDecision()
    action = CodexAction(
        kind="respond",
        instruction=instruction,
        subgoal="synthesize_answer",
        metadata={
            "direct_output": bool(parsed.get("direct_output")),
            "from_evaluator": True,
            "evaluator_reason": "llm_continue",
        },
    )
    return CodexEvaluationDecision(status="continue", next_action=action)


def _build_evaluator_progress_summary(task: CodexTask) -> str:
    completed_actions = [action for action in task.actions if action.status == "completed"]
    pending_actions = [action for action in task.actions if action.status != "completed"]
    lines = [
        f"- task_profile: {task.task_profile}",
        f"- completed_actions: {len(completed_actions)}",
        f"- remaining_actions: {len(pending_actions)}",
    ]
    if task.plan is not None:
        plan_tasks = list(getattr(task.plan, "tasks", []) or [])
        completed_plan = sum(1 for item in plan_tasks if getattr(item, "status", "") == "completed")
        lines.append(f"- plan_status: {getattr(task.plan, 'status', 'unknown')}")
        lines.append(f"- plan_tasks_completed: {completed_plan}/{len(plan_tasks)}")
        for item in plan_tasks[:6]:
            title = str(getattr(item, "title", "") or getattr(item, "kind", "task") or "task")
            lines.append(f"  - [{getattr(item, 'status', 'pending')}] {title}")
    if task.memory.read_paths:
        lines.append("- read_paths: " + ", ".join(task.memory.read_paths[-6:]))
    if task.memory.modified_paths:
        lines.append("- modified_paths: " + ", ".join(task.memory.modified_paths[-6:]))
    if task.memory.known_facts:
        lines.append("- recent_known_facts:")
        lines.extend(f"  - {fact}" for fact in task.memory.known_facts[-6:])
    if task.memory.open_questions:
        lines.append("- open_questions:")
        lines.extend(f"  - {question}" for question in task.memory.open_questions[-4:])
    else:
        lines.append("- open_questions: none")
    if task.memory.pending_verifications:
        lines.append("- pending_verifications:")
        lines.extend(f"  - {item}" for item in task.memory.pending_verifications[-4:])
    else:
        lines.append("- pending_verifications: none")
    return "\n".join(lines)


def _is_list_only_request(goal: str) -> bool:
    return False


def _synthesize_knowledge_answer(task: CodexTask, completed: list[CodexAction]) -> str:
    last = completed[-1]
    tool_name = str(last.metadata.get("tool_name") or "").strip()
    observation = (last.observation or "").strip()
    if not observation:
        return ""
    if task.task_profile == "repository_explainer":
        repository_summary = _build_repository_claim_summary(task)
        if repository_summary:
            return repository_summary
    role_summary = _build_claim_based_answer(
        task.goal,
        task.memory.claims or _extract_claims_from_observation(observation),
        task.memory.typed_claims,
    )
    if role_summary:
        return role_summary
    if tool_name == "inspect_workspace_path":
        return f"Here is a summary of the structure and key file roles for `{_extract_target_label(task.goal)}`:\n{observation}"
    if tool_name == "summarize_workspace_text":
        return f"Here is an initial summary based on your question:\n{observation}"
    if tool_name == "read_workspace_excerpt":
        return f"Here is a brief explanation based on the key excerpt:\n{observation}"
    if tool_name == "read_workspace_text":
        return f"Here is an initial explanation based on the file content:\n{_summarize_read_content(observation)}"
    return observation


def _build_repository_claim_summary(task: CodexTask) -> str:
    structure_claims = [claim for claim in task.memory.typed_claims if claim.get("kind") == "structure"]
    role_claims = [claim for claim in task.memory.typed_claims if claim.get("kind") == "role"]
    lines: list[str] = []
    if structure_claims:
        lines.append(f"Directory structure: {structure_claims[0].get('detail', '')}")
    for claim in role_claims[:4]:
        subject = str(claim.get("subject") or "").strip()
        detail = str(claim.get("detail") or "").strip()
        if subject and detail:
            lines.append(f"{subject}: {detail}")
    if not lines:
        return ""
    return "Based on collected information:\n" + "\n".join(f"- {line}" for line in lines)


def _build_claim_based_answer(goal: str, claims: list[str], typed_claims: list[dict[str, str]]) -> str:
    if not claims:
        return ""
    relevant = [claim for claim in claims if any(token in claim for token in _goal_target_tokens(goal))]
    selected = relevant or claims[:3]
    structure_claims = [claim for claim in typed_claims if claim.get("kind") == "structure"]
    role_claims = [claim for claim in typed_claims if claim.get("kind") == "role"]
    lines: list[str] = []
    if structure_claims:
        lines.append(f"Directory structure: {structure_claims[0].get('detail', '')}")
    if role_claims:
        for claim in role_claims[:3]:
            lines.append(f"{claim.get('subject', '')}: {claim.get('detail', '')}")
    elif selected:
        lines.extend(selected)
    if not lines:
        return ""
    return "Based on collected information:\n" + "\n".join(f"- {line}" for line in lines if line)


def _goal_target_tokens(goal: str) -> list[str]:
    return [token for token in re.split(r"[\s，。,:：]+", goal) if token and ("/" in token or "." in token or "_" in token)]


def _goal_prefers_summary(goal: str) -> bool:
    return False


def _relative_target_path(target_semantics: dict[str, Any], context: Any) -> str:
    path = str(target_semantics.get("path") or "").strip()
    root = str(context.application_context.config.get("default_directory") or "").strip()
    if path and root and path.startswith(root):
        relative = path[len(root) :].lstrip("/").lstrip("\\")
        return relative or Path(path).name
    return path


def _extract_claims_from_observation(observation: str) -> list[str]:
    claims: list[str] = []
    for line in observation.splitlines():
        match = re.match(r"-\s+([^:：]+)[:：](.+)", line.strip())
        if not match:
            continue
        target = match.group(1).strip()
        detail = match.group(2).strip()
        claims.append(f"{target}: {detail}")
    return claims


def _extract_target_label(goal: str) -> str:
    candidates = [token for token in goal.replace("，", " ").replace("。", " ").split() if "/" in token or "_" in token]
    return candidates[0] if candidates else "target"


def _summarize_read_content(observation: str) -> str:
    lines = [line.strip() for line in observation.splitlines() if line.strip()]
    if not lines:
        return observation[:240]
    preview = lines[:3]
    return "\n".join(f"- {line}" for line in preview)


def _extract_target_semantics(task: CodexTask, action: CodexAction) -> dict[str, Any] | None:
    plan = getattr(task, "plan", None)
    plan_semantics = getattr(plan, "target_semantics", None)
    if plan_semantics is not None and getattr(plan_semantics, "resource_kind", ""):
        return {
            "path": str(getattr(plan_semantics, "path", "") or ""),
            "resource_kind": str(getattr(plan_semantics, "resource_kind", "") or ""),
            "is_container": bool(getattr(plan_semantics, "is_container", False)),
            "allowed_actions": list(getattr(plan_semantics, "allowed_actions", []) or []),
        }
    result_payload = dict(action.metadata.get("result") or {})
    tool_output = dict(result_payload.get("tool_output") or {})
    tool_name = str(action.metadata.get("tool_name") or "").strip()
    path = str(tool_output.get("resolved_path") or tool_output.get("path") or action.metadata.get("arguments", {}).get("path") or "")
    resource_kind = str(tool_output.get("resource_kind") or "").strip()
    if not resource_kind and tool_name == "list_workspace_directory":
        resource_kind = "directory"
    if not resource_kind and tool_name in {"read_workspace_text", "summarize_workspace_text"}:
        resource_kind = "file"
    if not resource_kind:
        return None
    return {
        "path": path,
        "resource_kind": resource_kind,
        "is_container": bool(tool_output.get("is_container") or resource_kind == "directory"),
        "allowed_actions": [str(item).strip() for item in tool_output.get("allowed_actions") or [] if str(item).strip()],
    }


def _has_repository_structure_evidence(task: CodexTask) -> bool:
    if any(claim.get("kind") == "structure" for claim in task.memory.typed_claims):
        return True
    # list_workspace_directory results also count as valid structure evidence
    return any(
        action.status == "completed"
        and str(action.metadata.get("tool_name") or "") in {
            "list_workspace_directory",
            "inspect_workspace_path",
            "rank_workspace_entries",
            "extract_workspace_outline",
        }
        for action in task.actions
    )
