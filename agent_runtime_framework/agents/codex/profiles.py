from __future__ import annotations

import json
import re
from typing import Any

from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import build_codex_system_prompt, build_follow_up_context
from agent_runtime_framework.agents.codex.run_context import build_run_context_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime

_REPOSITORY_EXPLAINER_MARKERS = (
    "目录结构",
    "代码库",
    "仓库",
    "workspace",
    "repo",
    "package",
    "模块",
    "文件都是在做什么",
    "主要有哪些文件",
    "都有哪些文件",
    "下面都有什么",
    "里面都有什么",
    "下面都是在讲什么",
    "在讲什么",
    "讲什么",
    "作用",
    "功能",
    "architecture",
)

_REPOSITORY_TARGET_MARKERS = (
    "目录",
    "文件夹",
    "文件",
    "package",
    "模块",
    "代码库",
    "仓库",
    "workspace",
    "repo",
)

_CHANGE_AND_VERIFY_MARKERS = (
    "修改",
    "编辑",
    "替换",
    "创建",
    "删除",
    "移动",
    "重命名",
    "patch",
    "edit ",
    "verify",
    "运行验证",
    "运行测试",
)

_FILE_READER_MARKERS = (
    "读取",
    "读一下",
    "看看",
    "看下",
    "总结",
    "概括",
    "summarize",
    "summary",
    "read",
    "file",
    "文件内容",
    "主要内容",
)

_REPOSITORY_EXPLANATION_MARKERS = (
    "是什么",
    "做什么",
    "在讲什么",
    "讲什么",
    "干什么",
    "介绍",
    "讲解",
    "解释",
    "结构",
    "作用",
    "功能",
    "下面",
    "里面",
    "有哪些文件",
    "都有什么",
)

_CURRENT_DIRECTORY_MARKERS = (
    "当前目录",
    "当前工作区",
    "根目录",
    "workspace root",
)

_GENERIC_TARGET_TOKENS = {
    "package",
    "module",
    "directory",
    "folder",
    "workspace",
    "repo",
    "read",
    "summarize",
    "list",
}

_TARGET_HINT_PATTERN = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")


def classify_task_profile(user_input: str, context: Any | None = None, session: Any | None = None) -> str:
    llm_profile = _classify_task_profile_with_model(user_input, context, session=session)
    if llm_profile is not None:
        return llm_profile
    return _classify_task_profile_fallback(user_input)


def extract_workspace_target_hint(user_input: str) -> str:
    text = user_input.strip()
    lowered = text.lower()
    if any(marker in text for marker in _CURRENT_DIRECTORY_MARKERS) or any(marker in lowered for marker in _CURRENT_DIRECTORY_MARKERS):
        return ""

    ranked: list[tuple[int, int, str]] = []
    for match in _TARGET_HINT_PATTERN.finditer(text):
        candidate = match.group(0).strip("./")
        lowered_candidate = candidate.lower()
        if not candidate or lowered_candidate in _GENERIC_TARGET_TOKENS:
            continue
        score = 0
        if any(char in candidate for char in ("/", ".", "_")):
            score += 3
        if candidate.isascii() and candidate.isidentifier():
            score += 1
        window_start = max(0, match.start() - 6)
        window_end = min(len(text), match.end() + 8)
        window = text[window_start:window_end]
        lowered_window = window.lower()
        if any(marker in window for marker in ("目录", "文件夹", "模块", "下面", "里面", "代码库", "仓库")):
            score += 2
        if any(marker in lowered_window for marker in ("package", "module", "workspace", "repo")):
            score += 2
        ranked.append((score, len(candidate), candidate))
    if ranked:
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return ranked[0][2]

    tokens = [token.strip() for token in re.split(r"[\s，。,:：？?！!]+", text) if token.strip()]
    for token in tokens:
        lowered_token = token.lower()
        if lowered_token in _GENERIC_TARGET_TOKENS:
            continue
        if "/" in token or "." in token or "_" in token:
            return token
        if token.isascii() and token.isidentifier():
            return token
    return ""


def _classify_task_profile_with_model(user_input: str, context: Any | None = None, *, session: Any | None = None) -> str | None:
    if context is None:
        return None
    if not bool(getattr(context, "services", {}).get("model_first_task_profile_classifier")):
        return None
    runtime = resolve_model_runtime(context.application_context, "planner")
    llm_client = runtime.client if runtime is not None else context.application_context.llm_client
    model_name = runtime.profile.model_name if runtime is not None else context.application_context.llm_model
    if llm_client is None or not model_name:
        return None
    try:
        persona = resolve_runtime_persona(context, user_input=user_input)
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(
                        role="system",
                        content=build_codex_system_prompt(
                            "你负责做 task-profile classifier。只输出 JSON，格式为 "
                            '{"profile":"chat|repository_explainer|file_reader|change_and_verify"}。',
                            persona=persona,
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"用户请求：{user_input}\n"
                            + (build_run_context_block(context, session=session, user_input=user_input, persona=persona) + "\n" if context is not None else "")
                            +
                            "如果是在问代码库结构、目录内容、文件分布，用 repository_explainer。"
                            "如果是在读取、总结、解释某个具体文件内容，用 file_reader。"
                            "如果是在要求修改、创建、删除、验证，用 change_and_verify。"
                            "其他用 chat。"
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=80,
            ),
        )
    except Exception:
        return None
    try:
        parsed = json.loads(str(response.content or "").strip())
    except Exception:
        return None
    profile = str(parsed.get("profile") or "").strip()
    return profile if profile in {"chat", "repository_explainer", "file_reader", "change_and_verify"} else None


def _classify_task_profile_fallback(user_input: str) -> str:
    text = user_input.strip().lower()
    if any(marker.lower() in text for marker in _CHANGE_AND_VERIFY_MARKERS):
        return "change_and_verify"
    if any(marker.lower() in text for marker in _REPOSITORY_EXPLAINER_MARKERS):
        return "repository_explainer"
    if any(marker.lower() in text for marker in _REPOSITORY_TARGET_MARKERS) and any(
        marker.lower() in text for marker in _REPOSITORY_EXPLANATION_MARKERS
    ):
        return "repository_explainer"
    target_hint = extract_workspace_target_hint(user_input)
    if _looks_like_file_target(target_hint) and any(marker.lower() in text for marker in _FILE_READER_MARKERS):
        return "file_reader"
    if target_hint and not _looks_like_file_target(target_hint) and any(
        marker.lower() in text for marker in _REPOSITORY_EXPLANATION_MARKERS
    ):
        return "repository_explainer"
    return "chat"


def _looks_like_file_target(target_hint: str) -> bool:
    leaf = target_hint.rsplit("/", 1)[-1]
    if "." not in leaf:
        return False
    suffix = leaf.rsplit(".", 1)[-1].lower()
    return suffix.isalnum()
