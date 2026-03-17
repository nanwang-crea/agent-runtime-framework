from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime_framework.applications.core import ApplicationContext, ApplicationSpec
from agent_runtime_framework.applications.desktop_actions import DesktopActionHandlerRegistry
from agent_runtime_framework.applications.structured import run_stage_parser
from agent_runtime_framework.core.models import Observation
from agent_runtime_framework.policy import PermissionLevel
from agent_runtime_framework.resources import ResolveRequest, ResourceRef


@dataclass(slots=True)
class DesktopIntent:
    user_input: str
    action: str
    target_name: str | None = None
    use_last_focus: bool = False


@dataclass(slots=True)
class DesktopAction:
    name: str
    permission_level: PermissionLevel
    resources: list[ResourceRef] = field(default_factory=list)


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


def _interpret(user_input: str, _context: ApplicationContext) -> DesktopIntent:
    return run_stage_parser(
        context=_context,
        service_name="intent_parser",
        service_args=(user_input,),
        llm_system_prompt=(
            "你是桌面内容助手的意图解析器。"
            "请只输出合法 JSON，字段为 action、target_name、use_last_focus。"
            "action 只能是 list、read、summarize。"
        ),
        llm_user_prompt=user_input.strip(),
        normalizer=lambda parsed: _normalize_llm_intent(parsed, user_input),
        fallback=lambda: _interpret_with_rules(user_input),
        max_tokens=200,
    )


def _normalize_llm_intent(parsed: dict, user_input: str) -> DesktopIntent | None:
    action = str(parsed.get("action") or "").strip().lower()
    if action not in {"list", "read", "summarize"}:
        return None
    target_name = str(parsed.get("target_name") or "").strip() or None
    return DesktopIntent(
        user_input=user_input.strip(),
        action=action,
        target_name=target_name,
        use_last_focus=bool(parsed.get("use_last_focus")),
    )


def _interpret_with_rules(user_input: str) -> DesktopIntent:
    text = user_input.strip()
    if any(marker in text for marker in ("列出", "列一下", "有哪些文件", "有什么文件")):
        return DesktopIntent(
            user_input=text,
            action="list",
            target_name=_extract_target_name(text, action="list"),
        )
    if "总结" in text or "总结一下" in text:
        return DesktopIntent(user_input=text, action="summarize", target_name=_extract_target_name(text, action="summarize"))
    if "读取" in text or "看" in text:
        return DesktopIntent(
            user_input=text,
            action="read",
            target_name=_extract_target_name(text, action="read"),
            use_last_focus=("刚才" in text or "那个文件" in text or "上一个" in text),
        )
    return DesktopIntent(user_input=text, action="list")


def _extract_target_name(text: str, *, action: str) -> str | None:
    for marker in (
        "读取",
        "总结",
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


def _resolve(intent: DesktopIntent, context: ApplicationContext) -> list[ResourceRef]:
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


def _plan(intent: DesktopIntent, resources: list[ResourceRef], _context: ApplicationContext) -> list[DesktopAction]:
    action_names = run_stage_parser(
        context=_context,
        service_name="planner_parser",
        service_args=(intent, resources),
        llm_system_prompt=(
            "你是桌面内容助手的动作规划器。"
            "请只输出合法 JSON，字段为 actions。"
            "actions 是动作名数组，成员只能是 list、read、summarize。"
        ),
        llm_user_prompt=(
            f"用户输入：{intent.user_input}\n"
            f"当前意图动作：{intent.action}\n"
            f"已解析资源数量：{len(resources)}"
        ),
        normalizer=_normalize_planned_actions,
        fallback=lambda: [intent.action if intent.action in {"list", "read", "summarize"} else "list"],
        max_tokens=200,
    )
    return [
        DesktopAction(
            name=action_name,
            permission_level=_permission_for_action(action_name),
            resources=resources,
        )
        for action_name in action_names
    ]


def _normalize_resolve_hints(parsed: dict) -> DesktopResolveHints | None:
    if not isinstance(parsed, dict):
        return None
    target_name = str(parsed.get("target_name") or "").strip() or None
    return DesktopResolveHints(
        target_name=target_name,
        use_last_focus=bool(parsed.get("use_last_focus")),
        use_default_directory=bool(parsed.get("use_default_directory")),
    )


def _normalize_planned_actions(parsed: dict) -> list[str] | None:
    if not isinstance(parsed, dict):
        return None
    raw_actions = parsed.get("actions")
    if not isinstance(raw_actions, list):
        return None
    actions: list[str] = []
    for item in raw_actions:
        action = str(item or "").strip().lower()
        if action in {"list", "read", "summarize"} and action not in actions:
            actions.append(action)
    return actions or None


def _permission_for_action(action_name: str) -> PermissionLevel:
    if action_name == "list":
        return PermissionLevel.METADATA_READ
    return PermissionLevel.CONTENT_READ


def _normalize_execution_options(parsed: dict) -> DesktopExecutionOptions | None:
    if not isinstance(parsed, dict):
        return None
    mode = str(parsed.get("mode") or "").strip().lower()
    if mode not in {"full", "preview"}:
        return None
    return DesktopExecutionOptions(mode=mode)


def _normalize_compose_options(parsed: dict) -> DesktopComposeOptions | None:
    if not isinstance(parsed, dict):
        return None
    if "text_prefix" in parsed:
        return DesktopComposeOptions(text_prefix=str(parsed.get("text_prefix") or ""))
    if "text" in parsed:
        text_value = str(parsed.get("text") or "")
        return DesktopComposeOptions(text_prefix=text_value.replace("{text}", ""))
    return None


def _authorize(action: DesktopAction, context: ApplicationContext, *, confirmed: bool):
    return context.policy.authorize(action.permission_level, confirmed=confirmed)


def _execute(action: DesktopAction, context: ApplicationContext, _working_memory) -> dict:
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
    return registry.execute(
        action.name,
        resources=action.resources,
        context=context,
        execution_mode=execution_options.mode,
    )


def _compose(outcome: dict, _context: ApplicationContext) -> tuple[str, list[Observation]]:
    if outcome.get("kind") == "list":
        text = str(outcome.get("text") or "")
        observations = [Observation(kind=outcome.get("kind", "result"), payload={"text": text})]
        return text, observations

    compose_options = run_stage_parser(
        context=_context,
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


def _remember(outcome: dict, context: ApplicationContext) -> None:
    context.session_memory.remember_focus(
        list(outcome.get("focused_resources", [])),
        summary=outcome.get("text", "")[:200] or None,
    )


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
    )
