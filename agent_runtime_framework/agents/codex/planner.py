from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent_runtime_framework.agents.codex.models import CodexAction
from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import (
    build_codex_system_prompt,
    build_resource_semantics_block,
    build_tool_guidance_lines,
    extract_task_resource_semantics,
)
from agent_runtime_framework.agents.codex.profiles import extract_workspace_target_hint
from agent_runtime_framework.agents.codex.run_context import available_tool_names, build_run_context_block
from agent_runtime_framework.agents.codex.workflows import workflow_name_for_task_profile
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime

logger = logging.getLogger(__name__)


def plan_codex_actions(user_input: str) -> list[CodexAction]:
    action = _plan_from_goal(user_input, tool_names=set())
    return [action] if action is not None else []


def plan_next_codex_action(task: Any, session: Any, context: Any) -> CodexAction | None:
    completed = [action for action in task.actions if action.status == "completed"]
    if not completed:
        deterministic = _plan_first_action_by_profile(task, context)
        if deterministic is not None:
            return deterministic
    if completed:
        last_action = completed[-1]
        if last_action.kind == "respond":
            return None
        profile = str(getattr(task, "task_profile", "") or "chat")
        tool_name = str(getattr(last_action, "metadata", {}).get("tool_name") or "")
        if last_action.observation and profile == "chat":
            return CodexAction(
                kind="respond",
                instruction=last_action.observation,
                subgoal="synthesize_answer",
                metadata={"direct_output": True},
            )
        if last_action.observation and profile == "file_reader" and tool_name in {"read_workspace_text", "read_workspace_excerpt", "summarize_workspace_text", "inspect_workspace_path"}:
            return CodexAction(
                kind="respond",
                instruction=_direct_file_reader_response(str(getattr(task, "goal", "") or ""), last_action.observation),
                subgoal="synthesize_answer",
                metadata={"direct_output": True},
            )
    llm_planned = _plan_next_action_with_llm(task, context, session=session)
    if llm_planned is not None:
        return llm_planned
    raise AppError(
        code="PLANNER_RUNTIME_MISSING",
        message="未配置可用的大模型，无法为 Codex Agent 规划下一步动作。",
        detail="planner model runtime is unavailable",
        stage="planner",
        retriable=True,
        suggestion="请先在前端“模型 / 配置”中配置并认证一个 planner 模型。",
    )


def _plan_first_action_by_profile(task: Any, context: Any) -> CodexAction | None:
    profile = str(getattr(task, "task_profile", "chat") or "chat")
    persona = resolve_runtime_persona(context, task=task)
    tool_names = set(available_tool_names(context, persona=persona))
    goal = str(getattr(task, "goal", "") or "")
    if profile in {"repository_explainer", "file_reader"} and "resolve_workspace_target" in tool_names:
        return CodexAction(
            kind="call_tool",
            instruction=goal,
            subgoal="gather_evidence",
            metadata={
                "tool_name": "resolve_workspace_target",
                "arguments": {
                    "query": goal,
                    "target_hint": _extract_repository_target(goal),
                },
            },
        )
    return None


def _extract_repository_target(goal: str) -> str:
    return extract_workspace_target_hint(goal)


def _plan_from_goal(user_input: str, *, tool_names: set[str]) -> CodexAction | None:
    text = user_input.strip()
    lowered = text.lower()

    verification_prefixes = ("运行验证", "运行测试", "run verification", "verify ")
    for prefix in verification_prefixes:
        if lowered.startswith(prefix.lower()) and "run_shell_command" in tool_names:
            command = text[len(prefix) :].strip()
            if command:
                return CodexAction(
                    kind="run_verification",
                    instruction=command,
                    subgoal="verify_changes",
                    metadata={"command": command},
                )

    patch_match = re.search(
        r'把\s+([^\s]+)\s+里(?:的)?\s+"([^"]+)"\s+替换成\s+"([^"]+)"',
        text,
    )
    if patch_match and "apply_text_patch" in tool_names:
        path, search_text, replace_text = patch_match.groups()
        return CodexAction(
            kind="apply_patch",
            instruction=text,
            subgoal="modify_workspace",
            risk_class="high",
            metadata={
                "tool_name": "apply_text_patch",
                "arguments": {
                    "path": path,
                    "search_text": search_text,
                    "replace_text": replace_text,
                },
            },
        )

    replace_match = re.search(
        r'替换\s+([^\s]+)\s+里(?:的)?\s+"([^"]+)"\s+(?:为|成)\s+"([^"]+)"',
        text,
    )
    if replace_match and "replace_workspace_text" in tool_names:
        path, search_text, replace_text = replace_match.groups()
        return CodexAction(
            kind="edit_text",
            instruction=text,
            subgoal="modify_workspace",
            risk_class="high",
            metadata={
                "tool_name": "replace_workspace_text",
                "arguments": {
                    "path": path,
                    "search_text": search_text,
                    "replace_text": replace_text,
                },
            },
        )

    append_match = re.search(r"在\s+([^\s]+)\s+末尾追加\s+\"([^\"]+)\"", text)
    if append_match and "append_workspace_text" in tool_names:
        path, content = append_match.groups()
        return CodexAction(
            kind="edit_text",
            instruction=text,
            subgoal="modify_workspace",
            risk_class="high",
            metadata={
                "tool_name": "append_workspace_text",
                "arguments": {
                    "path": path,
                    "content": bytes(content, "utf-8").decode("unicode_escape"),
                },
            },
        )

    create_match = re.search(r"(?:创建|新建)\s+([^\s]+)(?:\s+内容\s+(.+))?", text)
    if create_match and "create_workspace_path" in tool_names:
        path, content = create_match.groups()
        is_directory = any(marker in text for marker in ("文件夹", "目录", "folder", "directory"))
        return CodexAction(
            kind="create_path",
            instruction=text,
            subgoal="modify_workspace",
            risk_class="high",
            metadata={
                "tool_name": "create_workspace_path",
                "arguments": {
                    "path": path,
                    "content": (content or "").strip(),
                    "kind": "directory" if is_directory else "file",
                },
            },
        )

    edit_match = re.search(r"(?:编辑|修改)\s+([^\s]+)\s+内容\s+(.+)", text)
    if edit_match and "edit_workspace_text" in tool_names:
        path, content = edit_match.groups()
        return CodexAction(
            kind="edit_text",
            instruction=text,
            subgoal="modify_workspace",
            risk_class="high",
            metadata={
                "tool_name": "edit_workspace_text",
                "arguments": {"path": path, "content": content.strip()},
            },
        )

    move_match = re.search(r"把\s+([^\s]+)\s+(?:移动\s*到|重命名\s*到)\s+([^\s]+)", text)
    if move_match and "move_workspace_path" in tool_names:
        path, destination_path = move_match.groups()
        return CodexAction(
            kind="move_path",
            instruction=text,
            subgoal="modify_workspace",
            risk_class="high",
            metadata={
                "tool_name": "move_workspace_path",
                "arguments": {"path": path, "destination_path": destination_path},
            },
        )

    delete_match = re.search(r"(?:删除|delete)\s+([^\s]+)", text, flags=re.IGNORECASE)
    if delete_match and "delete_workspace_path" in tool_names:
        path = delete_match.group(1).strip()
        return CodexAction(
            kind="delete_path",
            instruction=text,
            subgoal="modify_workspace",
            risk_class="destructive",
            metadata={
                "tool_name": "delete_workspace_path",
                "arguments": {"path": path},
            },
        )

    summarize_match = re.search(r"(?:总结|概括|summarize)\s*([^\s]+)?", text, flags=re.IGNORECASE)
    if summarize_match and "summarize_workspace_text" in tool_names:
        path = (summarize_match.group(1) or "").strip()
        return CodexAction(
            kind="call_tool",
            instruction=text,
            subgoal="gather_evidence",
            metadata={
                "tool_name": "summarize_workspace_text",
                "arguments": {
                    "path": path,
                    "use_last_focus": any(marker in text for marker in ("刚才", "那个文件", "上一个")),
                },
            },
        )

    if any(marker in text for marker in ("列出", "列一下")) or lowered.startswith("list "):
        if "list_workspace_directory" in tool_names:
            path_match = re.search(r"(?:列出|列一下|list)\s*([^\s]+)?", text, flags=re.IGNORECASE)
            path = (path_match.group(1) or "").strip() if path_match else ""
            return CodexAction(
                kind="call_tool",
                instruction=text,
                subgoal="gather_evidence",
                metadata={
                    "tool_name": "list_workspace_directory",
                    "arguments": {
                        "path": path,
                        "use_last_focus": any(marker in text for marker in ("下面", "刚才", "上一个")),
                        "use_default_directory": not path,
                    },
                },
            )

    read_match = re.search(r"(?:读取|read)\s+([^\s]+)", text, flags=re.IGNORECASE)
    if read_match and "read_workspace_text" in tool_names:
        path = read_match.group(1).strip()
        return CodexAction(
            kind="call_tool",
            instruction=text,
            subgoal="gather_evidence",
            metadata={"tool_name": "read_workspace_text", "arguments": {"path": path}},
        )

    return CodexAction(kind="respond", instruction=text, subgoal="synthesize_answer")


def _plan_next_action_with_llm(task: Any, context: Any, *, session: Any | None = None) -> CodexAction | None:
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    persona = resolve_runtime_persona(context, task=task)
    tool_names = available_tool_names(context, persona=persona)
    if llm_client is None or not tool_names:
        return None
    tool_lines = build_tool_guidance_lines(context, tool_names)
    action_lines = [
        f"- kind: {action.kind}; status: {action.status}; instruction: {action.instruction}; observation: {action.observation or ''}"
        for action in task.actions[-6:]
    ]
    system_prompt = build_codex_system_prompt(
        "你负责做 next-action planner。请根据任务目标、最近 observation、资源语义、历史上下文和可用工具，选择唯一的下一步动作。"
        "只输出合法 JSON，字段允许为 kind、instruction、tool_name、arguments、risk_class、direct_output。"
        "kind 只能是 call_tool、apply_patch、move_path、delete_path、run_verification、respond。"
        "不要输出 action、tool、task、conversation 等其他 kind。"
        ,
        workflow_name=workflow_name_for_task_profile(str(getattr(task, "task_profile", "") or "")),
        persona=persona,
    )
    run_context_block = build_run_context_block(
        context,
        task=task,
        session=session,
        user_input=str(getattr(task, "goal", "") or ""),
        persona=persona,
    )
    semantics = extract_task_resource_semantics(task)
    preferred_file_tool = "summarize_workspace_text" if _goal_prefers_summary(str(getattr(task, "goal", "") or "")) else "read_workspace_text"
    user_prompt = (
        f"任务目标：{task.goal}\n"
        f"任务模式：{getattr(task, 'task_profile', 'chat')}\n"
        f"runtime persona：{persona.name}\n"
        f"{build_resource_semantics_block(task)}\n"
        f"{run_context_block}\n"
        f"最近动作：\n{chr(10).join(action_lines) if action_lines else '(none)'}\n"
        f"可用工具：\n{chr(10).join(tool_lines)}\n"
        f"工作区根目录：{context.application_context.config.get('default_directory', '')}\n"
        "约束：\n"
        "- 只能选择可用工具列表中的 tool_name\n"
        "- 写操作必须设置合适的 risk_class\n"
        "- destructive_write 对应 destructive\n"
        "- safe_write 对应 high\n"
        "- content_read / metadata_read 对应 low\n"
        "- repository_explainer 模式优先 resolve_workspace_target，再决定 inspect / read / list，最后综合回答\n"
        f"- file_reader 模式优先 resolve_workspace_target；当 resource_kind=file 且 allowed_actions={', '.join(semantics.get('allowed_actions') or []) or '(unknown)'} 时，优先 {preferred_file_tool}，再综合回答\n"
        "- change_and_verify 模式优先 edit / patch / write，然后运行验证，再总结\n"
        "- chat 模式优先直接回答，除非用户明确要求查看工作区或修改代码\n"
        f"- 当前 persona 的 evidence_threshold 是 {persona.evidence_threshold}，证据不足时优先继续收集而不是过早结束\n"
        "示例：\n"
        '- 如果用户说“memory 文件夹下面有什么”，优先输出 {"kind":"call_tool","tool_name":"resolve_workspace_target","arguments":{"query":"memory 文件夹下面有什么","target_hint":"memory"}}\n'
        '- 如果要运行 shell 命令 pwd，输出 {"kind":"call_tool","tool_name":"run_shell_command","arguments":{"command":"pwd"},"risk_class":"high"}\n'
        '- 如果要读取 README.md，输出 {"kind":"call_tool","tool_name":"read_workspace_text","arguments":{"path":"README.md"},"risk_class":"low"}\n'
        '- 如果只是直接回复用户，输出 {"kind":"respond","instruction":"..."}\n'
    )
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_prompt),
                ],
                temperature=0.0,
                max_tokens=220,
            ),
        )
    except Exception as exc:
        raise AppError(
            code="PLANNER_REQUEST_FAILED",
            message="planner 模型请求失败，无法生成下一步动作。",
            detail=f"{type(exc).__name__}: {exc}",
            stage="planner",
            retriable=True,
            suggestion="请检查 planner 模型配置、认证状态和网络连通性。",
        ) from exc

    raw_content = (response.content or "").strip()
    try:
        parsed = json.loads(_extract_json_block(raw_content))
    except Exception as exc:
        logger.warning("planner invalid json: raw=%s", raw_content[:400])
        raise AppError(
            code="PLANNER_INVALID_JSON",
            message="planner 模型已返回结果，但不是合法 JSON。",
            detail=raw_content[:400],
            stage="planner",
            retriable=True,
            suggestion="请检查 planner prompt 或切换到更稳定的模型。",
        ) from exc

    invalid_reason = _invalid_action_reason(parsed, tool_names=set(tool_names))
    planned = None if invalid_reason else _normalize_llm_action(parsed, tool_names=set(tool_names))
    if planned is None:
        detail = f"reason={invalid_reason or 'unknown'}; parsed={json.dumps(parsed, ensure_ascii=False)[:400]}"
        logger.warning("planner normalization failed: %s", detail)
        raise AppError(
            code="PLANNER_NORMALIZATION_FAILED",
            message="planner 模型返回了 JSON，但内容不符合动作约束。",
            detail=detail,
            stage="planner",
            retriable=True,
            suggestion="请检查 tool_name、kind 和 arguments 是否满足约束。",
        )
    return planned


def _extract_json_block(text: str) -> str:
    stripped = text.strip()
    if "```" in stripped:
        stripped = re.sub(r"^.*?```(?:json)?\s*", "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"\s*```.*$", "", stripped, flags=re.DOTALL)
    return stripped.strip()


def _goal_prefers_summary(goal: str) -> bool:
    lowered = goal.strip().lower()
    return any(marker in lowered for marker in ("总结", "概括", "summarize", "summary", "主要内容"))


def _goal_is_raw_read(goal: str) -> bool:
    lowered = goal.strip().lower()
    return any(lowered.startswith(prefix) for prefix in ("读取", "read ")) and not _goal_prefers_summary(goal)


def _direct_file_reader_response(goal: str, observation: str) -> str:
    text = observation.strip()
    if _goal_is_raw_read(goal):
        return text
    target = extract_workspace_target_hint(goal) or "目标文件"
    return f"关于 `{target}`，我整理出的主要内容如下：\n{text}"


def _normalize_llm_action(parsed: dict[str, Any], *, tool_names: set[str] | None = None) -> CodexAction | None:
    if _invalid_action_reason(parsed, tool_names=tool_names) is not None:
        return None
    kind = str(parsed.get("kind") or "").strip()
    instruction = str(parsed.get("instruction") or "").strip()
    metadata = {
        "tool_name": str(parsed.get("tool_name") or "").strip(),
        "arguments": dict(parsed.get("arguments") or {}),
    }
    if kind == "respond" and bool(parsed.get("direct_output")):
        metadata["direct_output"] = True
    if kind == "apply_patch" and not metadata["tool_name"]:
        metadata["tool_name"] = "apply_text_patch"
    if kind == "move_path" and not metadata["tool_name"]:
        metadata["tool_name"] = "move_workspace_path"
    if kind == "delete_path" and not metadata["tool_name"]:
        metadata["tool_name"] = "delete_workspace_path"
    if kind == "run_verification" and not instruction:
        instruction = str(metadata["arguments"].get("command") or "")
    if tool_names is not None and metadata["tool_name"] and metadata["tool_name"] not in tool_names:
        return None
    if kind != "respond" and not metadata["tool_name"] and kind != "run_verification":
        return None
    if not instruction and kind == "respond":
        return None
    tool_name = metadata.get("tool_name") or ""
    risk_class = str(parsed.get("risk_class") or "low")
    if tool_name == "delete_workspace_path":
        risk_class = "destructive"
    elif tool_name in {"apply_text_patch", "move_workspace_path", "create_workspace_path", "edit_workspace_text"}:
        risk_class = "high"
    subgoal = "execute_step"
    if kind == "respond":
        subgoal = "synthesize_answer"
    elif kind == "run_verification":
        subgoal = "verify_changes"
    elif kind in {"call_tool"}:
        subgoal = "gather_evidence"
    elif kind in {"apply_patch", "move_path", "delete_path"} or tool_name in {
        "apply_text_patch",
        "move_workspace_path",
        "create_workspace_path",
        "edit_workspace_text",
        "delete_workspace_path",
    }:
        subgoal = "modify_workspace"
    return CodexAction(
        kind=kind,
        instruction=instruction or metadata["tool_name"],
        subgoal=subgoal,
        risk_class=risk_class,
        metadata=metadata,
    )


def _invalid_action_reason(parsed: dict[str, Any], *, tool_names: set[str] | None = None) -> str | None:
    if not isinstance(parsed, dict):
        return "parsed payload is not an object"
    kind = str(parsed.get("kind") or "").strip()
    if kind not in {"call_tool", "apply_patch", "move_path", "delete_path", "run_verification", "respond"}:
        return f"unsupported kind '{kind or '<empty>'}'"
    tool_name = str(parsed.get("tool_name") or "").strip()
    if kind == "apply_patch" and not tool_name:
        tool_name = "apply_text_patch"
    if kind == "move_path" and not tool_name:
        tool_name = "move_workspace_path"
    if kind == "delete_path" and not tool_name:
        tool_name = "delete_workspace_path"
    if tool_names is not None and tool_name and tool_name not in tool_names:
        return f"tool_name '{tool_name}' is not in available tools"
    if kind != "respond" and not tool_name and kind != "run_verification":
        return "non-respond action is missing tool_name"
    instruction = str(parsed.get("instruction") or "").strip()
    arguments = dict(parsed.get("arguments") or {})
    if kind == "respond" and not instruction:
        return "respond action is missing instruction"
    if kind == "run_verification" and not instruction and not str(arguments.get("command") or "").strip():
        return "run_verification action is missing command"
    return None


def _risk_hint_for_permission(permission_level: str) -> str:
    if permission_level == "destructive_write":
        return "destructive"
    if permission_level == "safe_write":
        return "high"
    return "low"
