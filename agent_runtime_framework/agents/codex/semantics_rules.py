from __future__ import annotations

from pathlib import Path
import re

from agent_runtime_framework.agents.codex.models import TaskIntent


_DIRECTORY_MARKERS = (
    "目录",
    "文件夹",
    "package",
    "packages",
    "module",
    "模块",
    "仓库",
    "项目",
    "代码库",
    "workspace",
    "工作区",
)
_DIRECTORY_EXPLANATION_MARKERS = (
    "主要",
    "功能",
    "讲些什么",
    "做什么",
    "结构",
    "介绍",
    "解释",
    "子文件",
    "职责",
    "作用",
    "梳理",
    "看看",
    "了解",
    "概览",
    "overview",
    "architecture",
    "负责",
)
_FILE_READ_MARKERS = ("读取", "阅读", "read", "打开", "内容", "总结", "概述", "摘要", "summary", "summarize", "说明")
_CHANGE_MARKERS = ("修改", "编辑", "更新", "替换", "追加", "重构", "新增", "添加", "创建", "删除", "移动", "rename", "patch", "fix", "实现", "改成")
_DEBUG_MARKERS = ("报错", "错误", "异常", "失败", "bug", "debug", "修复", "排查", "crash", "traceback")
_TEST_MARKERS = ("测试", "test", "pytest", "验证", "verify", "检查")
_SUMMARY_MARKERS = ("总结", "概述", "摘要", "summary", "summarize")
_RAW_READ_MARKERS = ("读取", "原文", "全文", "完整", "read", "打开")
_CURRENT_WORKSPACE_MARKERS = ("当前工作区", "当前目录", "当前工作目录", "工作目录", "当前项目", "这个项目", "整个仓库", "整个项目")
_STOPWORDS = {
    "the", "a", "an", "this", "that", "what", "how", "is", "are", "do", "does", "help", "me", "and", "or",
    "under", "folder", "directory", "module", "package", "repo", "project", "current", "workspace", "readme",
}
_FILE_EXTENSIONS = {".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".cjs", ".mjs", ".ini", ".cfg", ".sh"}


def infer_task_intent_from_keywords(user_input: str, workspace_root: Path | None = None) -> TaskIntent:
    text = str(user_input or "").strip()
    if not text:
        return TaskIntent(suggested_tool_chain=[])
    normalized = text.lower()
    target_hint = extract_target_hint(text, workspace_root)
    target_type = infer_target_type(target_hint, text, workspace_root)

    if contains_any(normalized, _DEBUG_MARKERS):
        return TaskIntent(
            task_kind="debug_and_fix",
            user_intent="debug_failure",
            goal_mode="debug",
            scope_kind=scope_kind_for_target(target_type, target_hint),
            target_ref=target_hint,
            target_hint=target_hint,
            target_type=target_type,
            target_confidence=0.72 if target_hint else 0.35,
            expected_output="fix_and_explanation",
            needs_grounding=True,
            needs_clarification=not bool(target_hint) and target_type == "unknown",
            allowed_strategy_family=["debug", "repair", "verification"],
            suggested_tool_chain=["resolve_workspace_target", "read_workspace_text", "run_shell_command"],
            confidence=0.82,
        )
    if contains_any(normalized, _CHANGE_MARKERS):
        return TaskIntent(
            task_kind="change_and_verify",
            user_intent="modify_workspace",
            goal_mode="modify",
            scope_kind=scope_kind_for_target(target_type, target_hint),
            target_ref=target_hint,
            target_hint=target_hint,
            target_type=target_type,
            target_confidence=0.72 if target_hint else 0.28,
            expected_output="change_summary",
            needs_grounding=True,
            needs_clarification=not bool(target_hint),
            allowed_strategy_family=["locate_modify_verify", "clarify_then_modify"],
            suggested_tool_chain=["resolve_workspace_target", "read_workspace_text", "apply_text_patch", "run_tests"],
            confidence=0.8,
        )
    if contains_any(normalized, _TEST_MARKERS) and not contains_any(normalized, _DIRECTORY_EXPLANATION_MARKERS):
        return TaskIntent(
            task_kind="test_and_verify",
            user_intent="run_verification",
            goal_mode="verify",
            scope_kind="workspace_root" if target_hint == "." else scope_kind_for_target(target_type, target_hint),
            target_ref=target_hint,
            target_hint=target_hint,
            target_type=target_type,
            target_confidence=0.6 if target_hint else 0.25,
            expected_output="verification_report",
            needs_grounding=True,
            needs_clarification=False,
            allowed_strategy_family=["verification_only"],
            suggested_tool_chain=["run_tests"],
            confidence=0.78,
        )
    if looks_like_repository_request(normalized, target_hint, target_type):
        workspace_target = target_hint or ("." if refers_to_current_workspace(text) or not target_hint else "")
        goal_mode = repository_goal_mode(normalized, text, workspace_target)
        return TaskIntent(
            task_kind="repository_explainer",
            user_intent="summarize_project" if goal_mode == "project_summary" else "explain_directory",
            goal_mode=goal_mode,
            scope_kind="workspace_root" if workspace_target == "." else scope_kind_for_target(target_type, workspace_target) or "directory",
            target_ref=workspace_target,
            target_hint=workspace_target,
            target_type="directory" if target_type == "unknown" else target_type,
            target_confidence=0.9 if workspace_target else 0.55,
            expected_output=goal_mode,
            needs_grounding=True,
            needs_clarification=False if workspace_target == "." else not bool(workspace_target),
            allowed_strategy_family=["workspace_overview", "repository_overview"],
            suggested_tool_chain=["resolve_workspace_target", "inspect_workspace_path", "rank_workspace_entries", "extract_workspace_outline", "respond"],
            confidence=0.9 if workspace_target else 0.74,
        )
    if looks_like_file_request(normalized, target_hint, target_type):
        preferred = "summarize_workspace_text" if contains_any(normalized, _SUMMARY_MARKERS) else "read_workspace_text"
        return TaskIntent(
            task_kind="file_reader",
            user_intent="summarize_file" if preferred == "summarize_workspace_text" else "read_file",
            goal_mode="file_summary" if preferred == "summarize_workspace_text" else "file_explanation",
            scope_kind="file",
            target_ref=target_hint,
            target_hint=target_hint,
            target_type="file" if target_type == "unknown" else target_type,
            target_confidence=0.85 if target_hint else 0.4,
            expected_output="summary" if preferred == "summarize_workspace_text" else "content_explanation",
            needs_grounding=True,
            needs_clarification=not bool(target_hint),
            allowed_strategy_family=["file_reader"],
            suggested_tool_chain=["resolve_workspace_target", preferred, "respond"],
            confidence=0.86 if target_hint else 0.68,
        )
    return TaskIntent(
        task_kind="chat",
        user_intent="general_chat",
        goal_mode="direct_answer",
        scope_kind="unknown",
        target_ref=target_hint,
        target_hint=target_hint,
        target_type=target_type,
        target_confidence=0.0,
        expected_output="direct_answer",
        needs_grounding=False,
        needs_clarification=False,
        allowed_strategy_family=["direct_answer"],
        suggested_tool_chain=["respond"],
        confidence=0.4,
    )


def goal_prefers_summary(goal: str) -> bool:
    return contains_any(str(goal or "").lower(), _SUMMARY_MARKERS)


def goal_is_raw_read(goal: str) -> bool:
    return contains_any(str(goal or "").lower(), _RAW_READ_MARKERS) and not goal_prefers_summary(goal)


def repository_target_hint(goal: str, workspace_root: Path | None = None) -> str:
    return extract_target_hint(goal, workspace_root)


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def scope_kind_for_target(target_type: str, target_hint: str) -> str:
    if target_hint == ".":
        return "workspace_root"
    if target_type == "directory":
        return "directory"
    if target_type == "file":
        return "file"
    return "unknown"


def looks_like_repository_request(normalized: str, target_hint: str, target_type: str) -> bool:
    if target_type == "directory":
        return True
    if refers_to_current_workspace(normalized):
        return True
    if contains_any(normalized, _DIRECTORY_MARKERS) and contains_any(normalized, _DIRECTORY_EXPLANATION_MARKERS):
        return True
    if target_hint and target_type == "unknown" and contains_any(normalized, _DIRECTORY_EXPLANATION_MARKERS):
        return True
    return False


def looks_like_file_request(normalized: str, target_hint: str, target_type: str) -> bool:
    if target_type == "file":
        return True
    if "readme" in normalized and contains_any(normalized, _FILE_READ_MARKERS):
        return True
    if contains_any(normalized, _FILE_READ_MARKERS) and bool(target_hint):
        return True
    return False


def repository_goal_mode(normalized: str, text: str, workspace_target: str) -> str:
    if looks_like_project_summary_request(normalized, text, workspace_target):
        return "project_summary"
    if looks_like_listing_request(normalized):
        return "workspace_listing"
    return "workspace_overview"


def looks_like_listing_request(normalized: str) -> bool:
    if contains_any(normalized, _DIRECTORY_EXPLANATION_MARKERS) or contains_any(normalized, _SUMMARY_MARKERS):
        return False
    return any(marker in normalized for marker in ("列", "list", "有哪些", "都有哪些"))


def looks_like_project_summary_request(normalized: str, text: str, workspace_target: str) -> bool:
    if not contains_any(normalized, _SUMMARY_MARKERS):
        return False
    if workspace_target == ".":
        return True
    return any(marker in text for marker in ("该项目", "这个项目", "当前项目", "整个项目", "整个仓库"))


def refers_to_current_workspace(text: str) -> bool:
    return any(marker in text for marker in _CURRENT_WORKSPACE_MARKERS)


def extract_target_hint(text: str, workspace_root: Path | None) -> str:
    if any(marker in text for marker in _CURRENT_WORKSPACE_MARKERS):
        return "."
    lowered = text.lower()
    if "readme" in lowered:
        explicit = existing_readme_candidate(workspace_root)
        return explicit or "README.md"
    candidates = [candidate for candidate in re.findall(r"[A-Za-z0-9_./-]+", text) if candidate not in {".", ".."}]
    existing = first_existing_candidate(candidates, workspace_root)
    if existing:
        return existing
    for candidate in candidates:
        cleaned = candidate.strip("./")
        if cleaned and cleaned.lower() not in _STOPWORDS and ("/" in cleaned or "." in cleaned or "_" in cleaned):
            return cleaned
    for candidate in candidates:
        cleaned = candidate.strip("./")
        if len(cleaned) >= 2 and cleaned.lower() not in _STOPWORDS:
            return cleaned
    return ""


def first_existing_candidate(candidates: list[str], workspace_root: Path | None) -> str:
    if workspace_root is None:
        return ""
    root = workspace_root.resolve()
    for candidate in candidates:
        cleaned = candidate.strip()
        if not cleaned:
            continue
        target = root if cleaned in {".", ""} else (root / cleaned).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.exists():
            return "." if target == root else str(target.relative_to(root))
    return ""


def infer_target_type(target_hint: str, text: str, workspace_root: Path | None) -> str:
    if target_hint == ".":
        return "directory"
    if str(target_hint).lower() in {"readme", "readme.md"}:
        return "file"
    if target_hint:
        suffix = Path(target_hint).suffix.lower()
        if suffix in _FILE_EXTENSIONS:
            return "file"
        if workspace_root is not None:
            target = (workspace_root / target_hint).resolve()
            if target.exists():
                if target.is_dir():
                    return "directory"
                if target.is_file():
                    return "file"
    if any(marker in text for marker in _DIRECTORY_MARKERS):
        return "directory"
    if "." in Path(target_hint).name and Path(target_hint).suffix:
        return "file"
    return "unknown"


def existing_readme_candidate(workspace_root: Path | None) -> str:
    if workspace_root is None:
        return ""
    root = workspace_root.expanduser().resolve()
    for candidate in ("README.md", "README", "readme.md", "readme"):
        target = (root / candidate).resolve()
        if target.exists() and target.is_file():
            return str(target.relative_to(root))
    return ""
