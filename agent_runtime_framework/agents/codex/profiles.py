from __future__ import annotations

import json
import re
from typing import Any

from agent_runtime_framework.agents.codex.personas import resolve_runtime_persona
from agent_runtime_framework.agents.codex.prompting import build_codex_system_prompt, build_follow_up_context
from agent_runtime_framework.agents.codex.run_context import build_run_context_block
from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime

_CURRENT_DIRECTORY_MARKERS = (
    "当前目录", "当前工作区", "根目录",
    "workspace root", "current directory", "root directory", "current workspace",
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
        if any(marker in window for marker in ("目录", "文件夹", "模块", "下面", "里面", "代码库", "仓库", "directory", "folder", "module")):
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
                            "You are a task-profile classifier. Output only JSON in the format: "
                            '{"profile":"chat|repository_explainer|file_reader|change_and_verify|debug_and_fix|multi_file_change|test_and_verify"}. '
                            "Choose exactly one profile from the enum above.",
                            persona=persona,
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"User request: {user_input}\n"
                            + (build_run_context_block(context, session=session, user_input=user_input, persona=persona) + "\n" if context is not None else "")
                            + "Profile selection rules:\n"
                            "- repository_explainer: listing files/directories, exploring workspace/repo structure, asking what files exist or what a directory contains.\n"
                            "- file_reader: reading, summarizing, or explaining a specific file's content.\n"
                            "- debug_and_fix: error, bug, crash, exception, debugging, or fix request.\n"
                            "- multi_file_change: refactoring, batch edits, renaming across multiple files.\n"
                            "- test_and_verify: running tests, analyzing test failures, checking test coverage.\n"
                            "- change_and_verify: modifying, creating, or deleting a single file or targeted code section.\n"
                            "- chat: anything else (questions, explanations, planning, general conversation).\n"
                            "Note: Use recent_turns and focused_resources in the runtime context above to clarify intent."
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
    return profile if profile in {"chat", "repository_explainer", "file_reader", "change_and_verify", "debug_and_fix", "multi_file_change", "test_and_verify"} else None


def _classify_task_profile_fallback(user_input: str) -> str:
    text = user_input.strip().lower()

    _MULTI_FILE_MARKERS = ("重构", "refactor", "所有文件", "所有调用", "批量", "统一修改", "全局替换", "所有引用", "rename across", "update all")
    if any(m in text for m in _MULTI_FILE_MARKERS):
        return "multi_file_change"

    _DEBUG_MARKERS = ("报错", "错误", "bug", "fix", "修复", "调试", "debug", "异常", "exception", "traceback", "失败", "崩溃", "问题出在", "哪里出错", "为什么报错", "不起作用", "不对")
    if any(m in text for m in _DEBUG_MARKERS):
        return "debug_and_fix"

    _CHANGE_MARKERS = ("修改", "编辑", "替换", "创建", "删除", "移动", "重命名", "patch", "edit ", "verify", "运行验证", "运行测试")
    if any(m in text for m in _CHANGE_MARKERS):
        return "change_and_verify"

    _TEST_MARKERS = ("跑测试", "跑一下测试", "跑个测试", "运行测试", "run tests", "pytest", "测试失败", "测试通过", "test suite", "测试覆盖", "验证测试")
    if any(m in text for m in _TEST_MARKERS):
        return "test_and_verify"

    _REPO_MARKERS = (
        "目录结构", "代码库", "仓库", "模块",
        "文件都是在做什么", "主要有哪些文件", "都有哪些文件",
        "下面都有什么", "里面都有什么", "下面都是在讲什么",
        "在讲什么", "讲什么", "作用", "功能",
        "列一下", "列出", "列举", "有哪些文件", "有什么文件", "有哪些",
        "确认一下", "当前目录", "当前工作区", "根目录", "有没有文件",
        "workspace", "repo", "package", "architecture", "structure",
        "what files", "list files", "show files", "overview",
    )
    if any(m in text for m in _REPO_MARKERS):
        return "repository_explainer"

    _REPO_EXPLANATION_MARKERS = (
        "是什么", "做什么", "在讲什么", "讲什么", "干什么", "介绍", "讲解", "解释",
        "结构", "作用", "功能", "下面", "里面", "有哪些文件", "都有什么",
        "what is", "what does", "explain", "describe", "summarize",
    )
    _REPO_TARGET_MARKERS = ("目录", "文件夹", "文件", "模块", "代码库", "仓库", "directory", "folder", "package")
    target_hint = extract_workspace_target_hint(user_input)
    if target_hint and not _looks_like_file_target(target_hint) and any(m in text for m in _REPO_EXPLANATION_MARKERS):
        return "repository_explainer"
    if any(m in text for m in _REPO_TARGET_MARKERS) and any(m in text for m in _REPO_EXPLANATION_MARKERS):
        return "repository_explainer"

    _FILE_READER_MARKERS = ("读取", "读一下", "看看", "看下", "总结", "概括", "summarize", "summary", "read", "file", "文件内容", "主要内容")
    if _looks_like_file_target(target_hint) and any(m in text for m in _FILE_READER_MARKERS):
        return "file_reader"

    return "chat"


def _looks_like_file_target(target_hint: str) -> bool:
    leaf = target_hint.rsplit("/", 1)[-1]
    if "." not in leaf:
        return False
    suffix = leaf.rsplit(".", 1)[-1].lower()
    return suffix.isalnum()


def is_list_only_request(goal: str) -> bool:
    """Return True for simple list/confirm-file requests with no deep explanation intent."""
    lowered = goal.strip().lower()
    _LIST_ONLY_MARKERS = (
        "列一下", "列出", "列举", "确认一下", "当前目录", "当前工作区", "根目录", "有没有文件",
        "list", "ls", "show files", "list files", "what files", "current directory", "root directory",
    )
    if not any(m.lower() in lowered for m in _LIST_ONLY_MARKERS):
        return False
    _DEEP_MARKERS = ("做什么", "是什么", "结构", "作用", "功能", "介绍", "总结", "解释", "讲解", "overview", "explain", "summarize", "describe", "what does", "purpose")
    return not any(m in lowered for m in _DEEP_MARKERS)
