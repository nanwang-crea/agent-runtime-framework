from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction, CodexEvaluationDecision, CodexTask
from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import build_codex_system_prompt
from agent_runtime_framework.agents.codex.run_context import build_run_context_block
from agent_runtime_framework.agents.codex.workflows import workflow_name_for_task_profile
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime

logger = logging.getLogger(__name__)

_KNOWLEDGE_MARKERS = (
    "总结",
    "概括",
    "介绍",
    "解释",
    "说明",
    "结构",
    "功能",
    "作用",
    "关系",
    "主要",
    "讲些什么",
    "summarize",
    "summary",
    "explain",
    "overview",
    "what",
    "how",
)


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
        for action in completed[-4:]
    ]
    tool_list = ", ".join(tool_names)
    persona = resolve_runtime_persona(context, task=task)
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=build_codex_system_prompt(
                            "你是 Codex agent 的 output evaluator。"
                            "判断当前任务是否应该 finish、continue 或 abstain。"
                            "只输出 JSON。"
                            '格式一：{"decision":"finish"}。'
                            '格式二：{"decision":"continue","kind":"call_tool|respond","instruction":"...","tool_name":"...","arguments":{},"direct_output":true|false}。'
                            '格式三：{"decision":"abstain"}。'
                            ,
                            workflow_name=workflow_name_for_task_profile(task.task_profile),
                            persona=persona,
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"任务目标：{task.goal}\n"
                            f"{build_run_context_block(context, task=task, session=session, user_input=task.goal, persona=persona)}\n"
                            f"最近已完成动作：\n{chr(10).join(action_lines)}\n"
                            f"可用工具：{tool_list}\n"
                            f"当前 persona evidence_threshold：{persona.evidence_threshold}\n"
                            "如果当前结果已经回答了用户目标，输出 finish。"
                            "如果当前结果只是原始工具输出，还需要补一步工具调用或综合回答，输出 continue。"
                            "如果你不确定，输出 abstain。"
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=220,
            ),
        )
    except Exception as exc:
        logger.warning("evaluator request failed: %s: %s", type(exc).__name__, exc)
        return CodexEvaluationDecision()
    raw_content = (response.content or "").strip()
    try:
        parsed = json.loads(_extract_json_block(raw_content))
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
    if not _is_knowledge_task(task.goal):
        return CodexEvaluationDecision()
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


def _is_knowledge_task(goal: str) -> bool:
    text = goal.strip().lower()
    return any(marker in text for marker in _KNOWLEDGE_MARKERS)


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
        return f"关于 `{_extract_target_label(task.goal)}`，我先整理了目录结构和关键文件职责：\n{observation}"
    if tool_name == "summarize_workspace_text":
        return f"按你的问题，我先概括如下：\n{observation}"
    if tool_name == "read_workspace_excerpt":
        return f"我先基于关键片段做一个简要说明：\n{observation}"
    if tool_name == "read_workspace_text":
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
            lines.append(f"{subject} 的作用是{detail}")
    if not lines:
        return ""
    return "基于已收集的信息，我的总结是：\n" + "\n".join(f"- {line}" for line in lines)


def _build_claim_based_answer(goal: str, claims: list[str], typed_claims: list[dict[str, str]]) -> str:
    if not claims:
        return ""
    relevant = [claim for claim in claims if any(token in claim for token in _goal_target_tokens(goal))]
    selected = relevant or claims[:3]
    structure_claims = [claim for claim in typed_claims if claim.get("kind") == "structure"]
    role_claims = [claim for claim in typed_claims if claim.get("kind") == "role"]
    if "作用" in goal or "功能" in goal or "role" in goal.lower():
        lines: list[str] = []
        if structure_claims:
            lines.append(f"目录结构：{structure_claims[0].get('detail', '')}")
        if role_claims:
            for claim in role_claims[:3]:
                lines.append(f"{claim.get('subject', '')} 的作用是{claim.get('detail', '')}")
        elif selected:
            lines.extend(selected)
        return "基于已收集的信息，我的总结是：\n" + "\n".join(f"- {line}" for line in lines if line)
    return ""


def _goal_target_tokens(goal: str) -> list[str]:
    return [token for token in re.split(r"[\s，。,:：]+", goal) if token and ("/" in token or "." in token or "_" in token)]


def _goal_prefers_summary(goal: str) -> bool:
    lowered = goal.strip().lower()
    return any(marker in lowered for marker in ("总结", "概括", "summarize", "summary", "主要内容"))


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
        claims.append(f"{target} 的作用是{detail}")
    return claims


def _extract_target_label(goal: str) -> str:
    candidates = [token for token in goal.replace("，", " ").replace("。", " ").split() if "/" in token or "_" in token]
    return candidates[0] if candidates else "目标内容"


def _summarize_read_content(observation: str) -> str:
    lines = [line.strip() for line in observation.splitlines() if line.strip()]
    if not lines:
        return observation[:240]
    preview = lines[:3]
    return "\n".join(f"- {line}" for line in preview)


def _extract_json_block(text: str) -> str:
    stripped = text.strip()
    if "```" in stripped:
        stripped = stripped.split("```", 1)[-1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


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
    return any(claim.get("kind") == "structure" for claim in task.memory.typed_claims)
