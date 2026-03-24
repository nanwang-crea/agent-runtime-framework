from __future__ import annotations

import json
import logging
from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction, CodexEvaluationDecision, CodexTask
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
    llm_decision = _evaluate_with_model(task, context, tool_names)
    if llm_decision.status != "abstain":
        if llm_decision.next_action is not None:
            llm_decision.next_action.metadata["evaluation_source"] = "model"
        return llm_decision
    fallback = _evaluate_deterministically(task, session, context, tool_names)
    if fallback.next_action is not None:
        fallback.next_action.metadata["evaluation_source"] = "fallback"
    return fallback


def _evaluate_with_model(task: CodexTask, context: Any, tool_names: list[str]) -> CodexEvaluationDecision:
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
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "你是 Codex agent 的 output evaluator。"
                            "判断当前任务是否应该 finish、continue 或 abstain。"
                            "只输出 JSON。"
                            '格式一：{"decision":"finish"}。'
                            '格式二：{"decision":"continue","kind":"call_tool|respond","instruction":"...","tool_name":"...","arguments":{},"direct_output":true|false}。'
                            '格式三：{"decision":"abstain"}。'
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"任务目标：{task.goal}\n"
                            f"最近已完成动作：\n{chr(10).join(action_lines)}\n"
                            f"可用工具：{tool_list}\n"
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
    if last_action.kind == "respond":
        return CodexEvaluationDecision(status="finish")
    if not _is_knowledge_task(task.goal):
        return CodexEvaluationDecision()
    if bool(last_action.metadata.get("from_evaluator")):
        return CodexEvaluationDecision()
    tool_name = str(last_action.metadata.get("tool_name") or "").strip()
    arguments = dict(last_action.metadata.get("arguments") or {})
    if tool_name == "list_workspace_directory" and "inspect_workspace_path" in tool_names:
        return CodexEvaluationDecision(
            status="continue",
            next_action=CodexAction(
                kind="call_tool",
                instruction=task.goal,
                metadata={
                    "tool_name": "inspect_workspace_path",
                    "arguments": {
                        "path": str(arguments.get("path") or ""),
                        "use_last_focus": True,
                    },
                    "from_evaluator": True,
                    "evaluator_reason": "directory_listing_needs_explanation",
                },
            ),
            summary="directory listing needs deeper inspection",
        )
    if tool_name in {"read_workspace_text", "summarize_workspace_text", "inspect_workspace_path"}:
        synthesized = _synthesize_knowledge_answer(task.goal, completed)
        if synthesized:
            return CodexEvaluationDecision(
                status="continue",
                next_action=CodexAction(
                    kind="respond",
                    instruction=synthesized,
                    metadata={"direct_output": True, "from_evaluator": True, "evaluator_reason": "synthesize_answer"},
                ),
                summary="raw evidence should be synthesized before finishing",
            )
    return CodexEvaluationDecision()


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
            metadata={"tool_name": tool_name, "arguments": arguments, "from_evaluator": True, "evaluator_reason": "llm_continue"},
        )
        return CodexEvaluationDecision(status="continue", next_action=action)
    if not instruction:
        return CodexEvaluationDecision()
    action = CodexAction(
        kind="respond",
        instruction=instruction,
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


def _synthesize_knowledge_answer(goal: str, completed: list[CodexAction]) -> str:
    last = completed[-1]
    tool_name = str(last.metadata.get("tool_name") or "").strip()
    observation = (last.observation or "").strip()
    if not observation:
        return ""
    if tool_name == "inspect_workspace_path":
        return f"关于 `{_extract_target_label(goal)}`，我先整理了目录结构和关键文件职责：\n{observation}"
    if tool_name == "summarize_workspace_text":
        return f"按你的问题，我先概括如下：\n{observation}"
    if tool_name == "read_workspace_text":
        return f"我先基于已读取内容做一个简要说明：\n{_summarize_read_content(observation)}"
    return observation


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
