from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from agent_runtime_framework.agents.codex.evidence_manager import evidence_gap
from agent_runtime_framework.agents.codex.models import CodexAction, CodexEvaluationDecision, CodexTask
from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import build_codex_system_prompt, extract_json_block, render_codex_prompt_doc
from agent_runtime_framework.agents.codex.run_context import build_run_context_block
from agent_runtime_framework.agents.codex.semantics import goal_is_raw_read, goal_prefers_summary
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
    system_prompt = build_codex_system_prompt(
        render_codex_prompt_doc("evaluator_system", workflow_name=workflow_name or "general"),
        workflow_name=workflow_name,
        persona=persona,
    )
    user_prompt = render_codex_prompt_doc(
        "evaluator_user",
        goal=task.goal,
        workflow_name=workflow_name or "(none)",
        run_context_block=build_run_context_block(context, task=task, session=session, user_input=task.goal, persona=persona),
        progress_summary=_build_evaluator_progress_summary(task),
        recent_completed_actions=chr(10).join(action_lines),
        available_tools=tool_list,
        evidence_threshold=persona.evidence_threshold,
    )
    for attempt in range(2):
        try:
            response = chat_once(
                llm_client,
                ChatRequest(
                    model=model_name,
                    messages=[
                        ChatMessage(role="system", content=system_prompt),
                        ChatMessage(role="user", content=user_prompt + ("\n\nRetry requirement: output evaluator JSON only." if attempt else "")),
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
            continue
        normalized = _normalize_evaluator_decision(parsed, tool_names=set(tool_names))
        if normalized.status != "abstain":
            return normalized
        logger.warning("evaluator normalization failed: parsed=%s", json.dumps(parsed, ensure_ascii=False)[:400])
    return CodexEvaluationDecision()


def _evaluate_deterministically(task: CodexTask, _session: Any, _context: Any, tool_names: list[str]) -> CodexEvaluationDecision:
    completed = [action for action in task.actions if action.status == "completed"]
    if not completed:
        return CodexEvaluationDecision()
    last_action = completed[-1]
    missing = evidence_gap(task)
    if last_action.kind == "respond" and not task.memory.open_questions and not task.memory.pending_verifications:
        return CodexEvaluationDecision(status="finish")
    if missing:
        return CodexEvaluationDecision(status="continue", summary="missing_evidence")
    return CodexEvaluationDecision(status="continue", summary="awaiting_synthesis")


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
    if decision == "continue":
        return CodexEvaluationDecision(status="continue", summary=str(parsed.get("summary") or "").strip())
    if decision != "abstain":
        return CodexEvaluationDecision()
    return CodexEvaluationDecision()


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
    synthesized = synthesize_answer(task)
    if synthesized and synthesized not in {task.goal, "目录结构信息不足。", "项目概览信息不足。", "文件内容信息不足。"}:
        return synthesized
    if task.task_profile == "file_reader" and tool_name in {"read_workspace_text", "read_workspace_excerpt", "summarize_workspace_text"}:
        if tool_name == "read_workspace_text":
            if goal_is_raw_read(task.goal):
                return observation
            return f"我先基于已读取内容做一个简要说明：\n{_summarize_read_content(observation)}"
        if tool_name == "read_workspace_excerpt":
            return f"我先基于关键片段做一个简要说明：\n{observation}"
        return f"我先基于已读取内容做一个简要说明：\n{observation}"
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
    if tool_name == "list_workspace_directory":
        key_files = ""
        if "Files:" in observation:
            key_files = "\n关键文件：" + observation.split("Files:", 1)[1].strip()
        return f"我先根据目录证据做一个结构说明：\n目录结构：\n{observation}{key_files}"
    if tool_name == "summarize_workspace_text":
        return f"我先基于已读取内容做一个简要说明：\n{observation}"
    if tool_name == "read_workspace_excerpt":
        return f"我先基于关键片段做一个简要说明：\n{observation}"
    if tool_name == "read_workspace_text":
        if goal_is_raw_read(task.goal):
            return observation
        return f"我先基于已读取内容做一个简要说明：\n{_summarize_read_content(observation)}"
    return observation


def _build_repository_claim_summary(task: CodexTask) -> str:
    structure_claims = [claim for claim in task.memory.typed_claims if claim.get("kind") == "structure"]
    role_claims = [claim for claim in task.memory.typed_claims if claim.get("kind") == "role"]
    lines: list[str] = []
    if structure_claims:
        lines.append(f"目录结构：{structure_claims[0].get('detail', '')}")
    for claim in role_claims[:4]:
        subject = str(claim.get("subject") or "").strip()
        detail = str(claim.get("detail") or "").strip()
        if subject and detail:
            lines.append(f"{subject} 的作用：{detail}")
    if not lines:
        return ""
    return "根据当前收集到的证据：\n" + "\n".join(f"- {line}" for line in lines)


def _build_claim_based_answer(goal: str, claims: list[str], typed_claims: list[dict[str, str]]) -> str:
    if not claims:
        return ""
    relevant = [claim for claim in claims if any(token in claim for token in _goal_target_tokens(goal))]
    selected = relevant or claims[:3]
    structure_claims = [claim for claim in typed_claims if claim.get("kind") == "structure"]
    role_claims = [claim for claim in typed_claims if claim.get("kind") == "role"]
    lines: list[str] = []
    if structure_claims:
        lines.append(f"目录结构：{structure_claims[0].get('detail', '')}")
    if role_claims:
        for claim in role_claims[:3]:
            lines.append(f"{claim.get('subject', '')} 的作用：{claim.get('detail', '')}")
    elif selected:
        lines.extend(selected)
    if not lines:
        return ""
    return "根据当前收集到的证据：\n" + "\n".join(f"- {line}" for line in lines if line)


def _goal_target_tokens(goal: str) -> list[str]:
    return [token for token in re.split(r"[\s，。,:：]+", goal) if token and ("/" in token or "." in token or "_" in token)]


def _goal_prefers_summary(goal: str) -> bool:
    return goal_prefers_summary(goal)


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
