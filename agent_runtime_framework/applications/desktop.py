from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime_framework.applications.core import ApplicationContext, ApplicationSpec
from agent_runtime_framework.applications.desktop_actions import DesktopActionHandlerRegistry
from agent_runtime_framework.applications.structured import run_stage_parser
from agent_runtime_framework.core.models import Observation
from agent_runtime_framework.policy import PermissionLevel, PolicyDecision
from agent_runtime_framework.resources import ResolveRequest, ResourceRef


WRITE_ACTIONS = {"create", "edit", "move", "delete"}
READ_ACTIONS = {"list", "read", "summarize"}
SUPPORTED_ACTIONS = WRITE_ACTIONS | READ_ACTIONS


@dataclass(slots=True)
class DesktopIntent:
    user_input: str
    action: str
    target_name: str | None = None
    destination_name: str | None = None
    content: str | None = None
    target_kind: str = "file"
    use_last_focus: bool = False


@dataclass(slots=True)
class DesktopAction:
    name: str
    permission_level: PermissionLevel
    resources: list[ResourceRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DesktopResolveHints:
    target_name: str | None = None
    use_last_focus: bool = False
    use_default_directory: bool = False


@dataclass(slots=True)
class DesktopExecutionOptions:
    mode: str = "full"


@dataclass(slots=True)
class DesktopComposeOptions:
    text_prefix: str = ""


def _default_directory_ref(context: ApplicationContext) -> ResourceRef:
    configured = context.config.get("default_directory")
    if configured:
        return ResourceRef.for_path(configured)
    root = context.resource_repository.allowed_roots[0]
    return ResourceRef.for_path(root)


def _interpret(user_input: str, context: ApplicationContext) -> DesktopIntent:
    return run_stage_parser(
        context=context,
        service_name="intent_parser",
        service_args=(user_input,),
        llm_system_prompt=(
            "你是桌面内容助手的意图解析器。"
            "请只输出合法 JSON，字段为 action、target_name、destination_name、content、use_last_focus。"
            "action 只能是 list、read、summarize、create、edit、move、delete。"
        ),
        llm_user_prompt=user_input.strip(),
        normalizer=lambda parsed: _normalize_llm_intent(parsed, user_input),
        fallback=lambda: _interpret_with_rules(user_input),
        max_tokens=260,
    )


def _normalize_llm_intent(parsed: dict[str, Any], user_input: str) -> DesktopIntent | None:
    action = str(parsed.get("action") or "").strip().lower()
    if action not in SUPPORTED_ACTIONS:
        return None
    target_name = str(parsed.get("target_name") or "").strip() or None
    destination_name = str(parsed.get("destination_name") or "").strip() or None
    content = parsed.get("content")
    target_kind = str(parsed.get("target_kind") or "file").strip().lower()
    if target_kind not in {"file", "directory"}:
        target_kind = "file"
    return DesktopIntent(
        user_input=user_input.strip(),
        action=action,
        target_name=target_name,
        destination_name=destination_name,
        content=str(content) if isinstance(content, str) else None,
        target_kind=target_kind,
        use_last_focus=bool(parsed.get("use_last_focus")),
    )


def _interpret_with_rules(user_input: str) -> DesktopIntent:
    text = user_input.strip()
    lowered = text.lower()
    if any(marker in text for marker in ("列出", "列一下", "有哪些文件", "有什么文件")):
        return DesktopIntent(user_input=text, action="list", target_name=_extract_target_name(text, action="list"))
    if "总结" in text or "summarize" in lowered:
        return DesktopIntent(user_input=text, action="summarize", target_name=_extract_target_name(text, action="summarize"))
    if "读取" in text or "read " in lowered or "看" in text:
        return DesktopIntent(
            user_input=text,
            action="read",
            target_name=_extract_target_name(text, action="read"),
            use_last_focus=("刚才" in text or "那个文件" in text or "上一个" in text),
        )
    if any(marker in text for marker in ("创建", "新建")) or "create " in lowered:
        target, content = _extract_target_and_content(text)
        target_kind = "directory" if any(marker in text for marker in ("文件夹", "目录", "folder", "directory")) else "file"
        if target_kind == "directory":
            target = _extract_directory_name(text) or target
            content = None
        return DesktopIntent(user_input=text, action="create", target_name=target, content=content, target_kind=target_kind)
    if any(marker in text for marker in ("编辑", "修改")) or "edit " in lowered:
        target, content = _extract_target_and_content(text)
        return DesktopIntent(user_input=text, action="edit", target_name=target, content=content)
    if any(marker in text for marker in ("移动", "重命名")) or "move " in lowered or "rename " in lowered:
        source, destination = _extract_move_pair(text)
        return DesktopIntent(user_input=text, action="move", target_name=source, destination_name=destination)
    if "删除" in text or "delete " in lowered or "remove " in lowered:
        return DesktopIntent(user_input=text, action="delete", target_name=_extract_target_name(text, action="delete"))
    return DesktopIntent(user_input=text, action="list")


def _extract_target_name(text: str, *, action: str) -> str | None:
    for marker in (
        "读取",
        "总结",
        "删除",
        "再看",
        "看",
        "列出",
        "列一下",
        "一下",
        "刚才那个文件",
        "都有哪些文件",
        "有哪些文件",
        "有什么文件",
        "下面",
        "目录",
        "吗",
        "呢",
        "可以给我",
        "我想知道",
    ):
        text = text.replace(marker, "")
    cleaned = " ".join(text.split()).strip().strip("。？！?!.，,").strip()
    if action == "list" and cleaned in {"当前", "当前目录", "这个", "这个目录"}:
        return None
    return cleaned or None


def _extract_target_and_content(text: str) -> tuple[str | None, str | None]:
    matched = re.search(r"(?:创建|新建|编辑|修改|create|edit)\s+([^\s，。,]+)", text, flags=re.IGNORECASE)
    target = matched.group(1).strip() if matched else None
    content_match = re.search(r"(?:内容|content)\s*[:：]?\s*(.+)$", text, flags=re.IGNORECASE)
    content = content_match.group(1).strip() if content_match else None
    return target, content


def _extract_move_pair(text: str) -> tuple[str | None, str | None]:
    matched = re.search(r"(?:移动|重命名|move|rename)\s+([^\s，。,]+)\s+(?:到|to)\s+([^\s，。,]+)", text, flags=re.IGNORECASE)
    if not matched:
        return None, None
    return matched.group(1).strip(), matched.group(2).strip()


def _extract_directory_name(text: str) -> str | None:
    matched = re.search(r"(?:创建|新建)\s*(?:文件夹|目录|folder|directory)\s+([^\s，。,]+)", text, flags=re.IGNORECASE)
    if not matched:
        return None
    return matched.group(1).strip()


def _resolve(intent: DesktopIntent, context: ApplicationContext) -> list[ResourceRef]:
    if intent.action == "create":
        return []
    snapshot = context.session_memory.snapshot()
    default_directory = _default_directory_ref(context)
    hints = run_stage_parser(
        context=context,
        service_name="resolver_parser",
        service_args=(intent, snapshot, default_directory),
        llm_system_prompt=(
            "你是桌面内容助手的资源定位解析器。"
            "请只输出合法 JSON，字段为 target_name、use_last_focus、use_default_directory。"
        ),
        llm_user_prompt=(
            f"用户输入：{intent.user_input}\n"
            f"当前动作：{intent.action}\n"
            f"默认目录：{default_directory.location}\n"
            f"最近焦点数量：{len(snapshot.focused_resources)}"
        ),
        normalizer=_normalize_resolve_hints,
        fallback=lambda: DesktopResolveHints(
            target_name=intent.target_name,
            use_last_focus=intent.use_last_focus,
            use_default_directory=(intent.action == "list"),
        ),
        max_tokens=200,
    )
    if hints.use_last_focus and snapshot.focused_resources:
        return list(snapshot.focused_resources)
    resolved = context.resource_resolver.resolve(
        ResolveRequest(
            user_input=intent.user_input,
            default_directory=default_directory,
            last_focused=snapshot.focused_resources,
        ),
        context.resource_repository,
    )
    if resolved:
        return resolved
    if hints.target_name:
        matches = context.resource_repository.find_by_name(default_directory, hints.target_name)
        if matches:
            return [matches[0]]
    if hints.use_default_directory:
        return [default_directory]
    return []


def _plan(intent: DesktopIntent, resources: list[ResourceRef], context: ApplicationContext) -> list[DesktopAction]:
    action_names = run_stage_parser(
        context=context,
        service_name="planner_parser",
        service_args=(intent, resources),
        llm_system_prompt=(
            "你是桌面内容助手的动作规划器。"
            "请只输出合法 JSON，字段为 actions。"
            "actions 是动作名数组，成员只能是 list、read、summarize、create、edit、move、delete。"
        ),
        llm_user_prompt=(
            f"用户输入：{intent.user_input}\n"
            f"当前意图动作：{intent.action}\n"
            f"已解析资源数量：{len(resources)}"
        ),
        normalizer=_normalize_planned_actions,
        fallback=lambda: [intent.action if intent.action in SUPPORTED_ACTIONS else "list"],
        max_tokens=220,
    )
    planned_actions: list[DesktopAction] = []
    for action_name in action_names:
        action_intent = DesktopIntent(
            user_input=intent.user_input,
            action=action_name,
            target_name=intent.target_name,
            destination_name=intent.destination_name,
            content=intent.content,
            target_kind=intent.target_kind,
            use_last_focus=intent.use_last_focus,
        )
        metadata: dict[str, Any] = {"intent": intent}
        if action_name in WRITE_ACTIONS:
            metadata["mutation_plan"] = _build_mutation_plan(action_intent, resources, context)
        planned_actions.append(
            DesktopAction(
                name=action_name,
                permission_level=_permission_for_action(action_name),
                resources=resources,
                metadata=metadata,
            )
        )
    return planned_actions


def _build_mutation_plan(intent: DesktopIntent, resources: list[ResourceRef], context: ApplicationContext) -> dict[str, Any]:
    default_directory = Path(_default_directory_ref(context).location)
    target_path = _resolve_target_path(default_directory, intent.target_name)
    destination_path = _resolve_target_path(default_directory, intent.destination_name) if intent.destination_name else None
    before_text = ""
    after_text = ""
    if intent.action in {"edit", "move", "delete"} and target_path is not None and target_path.exists() and target_path.is_file():
        before_text = target_path.read_text(encoding="utf-8")
    if intent.action == "create":
        if intent.target_kind == "directory":
            after_text = ""
        else:
            after_text = intent.content or ""
    elif intent.action == "edit":
        if intent.content is not None:
            after_text = intent.content
        else:
            after_text = before_text
    diff = _build_diff_preview(intent.action, target_path, destination_path, before_text, after_text, target_kind=intent.target_kind)
    preview = "\n".join(
        [
            "拟执行变更预览（确认后执行）：",
            f"- action: {intent.action}",
            f"- target: {target_path}" if target_path is not None else "- target: (missing)",
            f"- destination: {destination_path}" if destination_path is not None else "",
            "",
            diff,
        ]
    ).strip()
    summary = _mutation_summary(intent.action, target_path, destination_path)
    return {
        "action": intent.action,
        "target_path": str(target_path) if target_path is not None else "",
        "destination_path": str(destination_path) if destination_path is not None else "",
        "target_kind": intent.target_kind,
        "before_text": before_text,
        "after_text": after_text,
        "content": intent.content or "",
        "diff": diff,
        "preview": preview,
        "summary": summary,
    }


def _resolve_target_path(default_directory: Path, raw: str | None) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (default_directory / candidate).resolve()
    return candidate.resolve()


def _build_diff_preview(
    action: str,
    target_path: Path | None,
    destination_path: Path | None,
    before_text: str,
    after_text: str,
    *,
    target_kind: str = "file",
) -> str:
    if action == "create" and target_kind == "directory":
        return "\n".join(
            [
                "--- /dev/null",
                f"+++ {target_path}",
                "@@",
                f"+ mkdir: {target_path}",
            ]
        )
    if action in {"create", "edit"}:
        before_lines = before_text.splitlines()
        after_lines = after_text.splitlines()
        from_file = str(target_path) if action == "edit" and target_path is not None else "/dev/null"
        to_file = str(target_path) if target_path is not None else "unknown"
        diff_lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=from_file,
                tofile=to_file,
                lineterm="",
            )
        )
        return "\n".join(diff_lines) if diff_lines else "(no content change)"
    if action == "move":
        return "\n".join(
            [
                f"--- {target_path}",
                f"+++ {destination_path}",
                "@@",
                f"- move from: {target_path}",
                f"+ move to: {destination_path}",
            ]
        )
    if action == "delete":
        return "\n".join(
            [
                f"--- {target_path}",
                "+++ /dev/null",
                "@@",
                f"- delete: {target_path}",
            ]
        )
    return "(preview unavailable)"


def _mutation_summary(action: str, target_path: Path | None, destination_path: Path | None) -> str:
    if action == "create":
        return f"created: {target_path}"
    if action == "edit":
        return f"edited file: {target_path}"
    if action == "move":
        return f"moved file: {target_path} -> {destination_path}"
    if action == "delete":
        return f"deleted file: {target_path}"
    return "mutation completed"


def _normalize_resolve_hints(parsed: dict[str, Any]) -> DesktopResolveHints | None:
    if not isinstance(parsed, dict):
        return None
    target_name = str(parsed.get("target_name") or "").strip() or None
    return DesktopResolveHints(
        target_name=target_name,
        use_last_focus=bool(parsed.get("use_last_focus")),
        use_default_directory=bool(parsed.get("use_default_directory")),
    )


def _normalize_planned_actions(parsed: dict[str, Any]) -> list[str] | None:
    if not isinstance(parsed, dict):
        return None
    raw_actions = parsed.get("actions")
    if not isinstance(raw_actions, list):
        return None
    actions: list[str] = []
    for item in raw_actions:
        action = str(item or "").strip().lower()
        if action in SUPPORTED_ACTIONS and action not in actions:
            actions.append(action)
    return actions or None


def _permission_for_action(action_name: str) -> PermissionLevel:
    if action_name == "list":
        return PermissionLevel.METADATA_READ
    if action_name in {"read", "summarize"}:
        return PermissionLevel.CONTENT_READ
    if action_name == "delete":
        return PermissionLevel.DESTRUCTIVE_WRITE
    return PermissionLevel.SAFE_WRITE


def _normalize_execution_options(parsed: dict[str, Any]) -> DesktopExecutionOptions | None:
    if not isinstance(parsed, dict):
        return None
    mode = str(parsed.get("mode") or "").strip().lower()
    if mode not in {"full", "preview"}:
        return None
    return DesktopExecutionOptions(mode=mode)


def _normalize_compose_options(parsed: dict[str, Any]) -> DesktopComposeOptions | None:
    if not isinstance(parsed, dict):
        return None
    if "text_prefix" in parsed:
        return DesktopComposeOptions(text_prefix=str(parsed.get("text_prefix") or ""))
    if "text" in parsed:
        text_value = str(parsed.get("text") or "")
        return DesktopComposeOptions(text_prefix=text_value.replace("{text}", ""))
    return None


def _authorize(action: DesktopAction, context: ApplicationContext, *, confirmed: bool) -> PolicyDecision:
    decision = context.policy.authorize(action.permission_level, confirmed=confirmed)
    if not decision.requires_confirmation:
        return decision
    mutation_plan = action.metadata.get("mutation_plan")
    if not isinstance(mutation_plan, dict):
        return decision
    return PolicyDecision(
        allowed=True,
        requires_confirmation=True,
        reason="mutation_requires_confirmation",
        safe_alternative=str(mutation_plan.get("preview") or decision.safe_alternative or decision.reason),
    )


def _execute(action: DesktopAction, context: ApplicationContext, _working_memory) -> dict[str, Any]:
    execution_options = run_stage_parser(
        context=context,
        service_name="executor_parser",
        service_args=(action,),
        llm_system_prompt=(
            "你是桌面内容助手的执行策略解析器。"
            "请只输出合法 JSON，字段为 mode。"
            "mode 只能是 full 或 preview。"
        ),
        llm_user_prompt=(
            f"动作：{action.name}\n"
            f"资源数量：{len(action.resources)}"
        ),
        normalizer=_normalize_execution_options,
        fallback=lambda: DesktopExecutionOptions(mode="full"),
        max_tokens=120,
    )
    registry = context.services.get("action_handler_registry")
    if registry is None:
        registry = DesktopActionHandlerRegistry.default()
    context.services["_current_mutation_plan"] = action.metadata.get("mutation_plan")
    try:
        return registry.execute(
            action.name,
            resources=action.resources,
            context=context,
            execution_mode=execution_options.mode,
        )
    finally:
        context.services.pop("_current_mutation_plan", None)


def _compose(outcome: dict[str, Any], context: ApplicationContext) -> tuple[str, list[Observation]]:
    if outcome.get("kind") == "list":
        text = str(outcome.get("text") or "")
        observations = [Observation(kind=outcome.get("kind", "result"), payload={"text": text})]
        return text, observations

    compose_options = run_stage_parser(
        context=context,
        service_name="composer_parser",
        service_args=(outcome,),
        llm_system_prompt=(
            "你是桌面内容助手的回答组织器。"
            "请只输出合法 JSON，字段为 text_prefix。"
        ),
        llm_user_prompt=(
            f"结果类型：{outcome.get('kind', 'result')}\n"
            f"结果文本：{outcome.get('text', '')[:500]}"
        ),
        normalizer=_normalize_compose_options,
        fallback=lambda: DesktopComposeOptions(text_prefix=""),
        max_tokens=120,
    )
    text = f"{compose_options.text_prefix}{outcome.get('text', '')}"
    observations = [Observation(kind=outcome.get("kind", "result"), payload={"text": text})]
    return text, observations


def _remember(outcome: dict[str, Any], context: ApplicationContext) -> None:
    text = str(outcome.get("text") or "")
    context.session_memory.remember_focus(
        list(outcome.get("focused_resources", [])),
        summary=text[:200] or None,
    )
    if outcome.get("kind") in WRITE_ACTIONS and getattr(context, "artifact_store", None) is not None:
        run_context = dict(context.services.get("run_context") or {})
        record = context.artifact_store.add(
            "change_summary",
            title=str(outcome.get("kind")),
            content=text,
            metadata={
                "target": str(outcome.get("target_path") or ""),
                "destination": str(outcome.get("destination_path") or ""),
                "action": str(outcome.get("kind") or ""),
                "run_id": str(run_context.get("run_id") or ""),
                "task_id": str(run_context.get("task_id") or ""),
            },
        )
        recent = list(context.services.get("recent_artifact_ids") or [])
        recent.append(record.artifact_id)
        context.services["recent_artifact_ids"] = recent


def _rollback(
    completed_outcomes: list[dict[str, Any]],
    context: ApplicationContext,
    _working_memory,
    *,
    cause: Exception,
) -> dict[str, Any]:
    rolled_back = 0
    for outcome in reversed(completed_outcomes):
        rollback = outcome.get("rollback")
        if not isinstance(rollback, dict):
            continue
        kind = str(rollback.get("kind") or "")
        if kind == "delete_path":
            path = Path(str(rollback.get("path") or "")).expanduser().resolve()
            if path.exists():
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
                rolled_back += 1
            continue
        if kind == "restore_text":
            path = Path(str(rollback.get("path") or "")).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(rollback.get("content") or ""), encoding="utf-8")
            rolled_back += 1
            continue
        if kind == "move_path":
            from_path = Path(str(rollback.get("from_path") or "")).expanduser().resolve()
            to_path = Path(str(rollback.get("to_path") or "")).expanduser().resolve()
            if from_path.exists():
                to_path.parent.mkdir(parents=True, exist_ok=True)
                from_path.rename(to_path)
                rolled_back += 1
            continue
    if getattr(context, "artifact_store", None) is not None and rolled_back > 0:
        run_context = dict(context.services.get("run_context") or {})
        record = context.artifact_store.add(
            "rollback_summary",
            title="rollback",
            content=f"rolled back {rolled_back} action(s) due to: {type(cause).__name__}: {cause}",
            metadata={
                "run_id": str(run_context.get("run_id") or ""),
                "task_id": str(run_context.get("task_id") or ""),
                "rolled_back": rolled_back,
            },
        )
        recent = list(context.services.get("recent_artifact_ids") or [])
        recent.append(record.artifact_id)
        context.services["recent_artifact_ids"] = recent
    return {"rolled_back": rolled_back}


def create_desktop_content_application() -> ApplicationSpec:
    return ApplicationSpec(
        name="desktop_content_application",
        interpreter=_interpret,
        resolver=_resolve,
        planner=_plan,
        authorizer=_authorize,
        executor=_execute,
        composer=_compose,
        rememberer=_remember,
        rollbacker=_rollback,
    )
