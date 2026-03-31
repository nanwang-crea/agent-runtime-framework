from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime_framework.agents.codex.personas import RuntimePersona, resolve_runtime_persona, tool_access_for_persona
from agent_runtime_framework.sandbox import resolve_sandbox

_PENDING_CLARIFICATION_KEY = "codex:pending_clarification"


@dataclass(slots=True)
class RunContextToolSummary:
    name: str
    access: str
    permission_level: str
    description: str = ""
    risk_hint: str = "low"


@dataclass(slots=True)
class RunContextSnapshot:
    identity: dict[str, Any]
    workspace: dict[str, Any]
    permission_snapshot: dict[str, Any]
    loaded_instructions: list[str] = field(default_factory=list)
    recent_turns: list[str] = field(default_factory=list)
    focused_resources: list[str] = field(default_factory=list)
    recent_completed_actions: list[str] = field(default_factory=list)
    current_user_message: str = ""
    pending_clarification: str = ""
    pending_verification: list[str] = field(default_factory=list)
    current_plan_state: dict[str, Any] = field(default_factory=dict)
    memory_snapshot: dict[str, Any] = field(default_factory=dict)
    available_tools: list[RunContextToolSummary] = field(default_factory=list)


def build_run_context(
    context: Any,
    *,
    task: Any | None = None,
    session: Any | None = None,
    user_input: str = "",
    persona: RuntimePersona | None = None,
) -> RunContextSnapshot:
    active_persona = persona or resolve_runtime_persona(context, task=task, user_input=user_input)
    application_context = getattr(context, "application_context", context)
    config = getattr(application_context, "config", {}) or {}
    sandbox = resolve_sandbox(context)
    session_snapshot = application_context.session_memory.snapshot()
    focused_resources = [_format_resource(ref) for ref in getattr(session_snapshot, "focused_resources", [])[:5]]
    recent_turns = _recent_turns(session)
    workspace_root = str(sandbox.normalized_workspace_root())
    current_cwd = str(config.get("default_directory") or workspace_root)
    target_paths = _candidate_instruction_paths(task, session_snapshot)
    return RunContextSnapshot(
        identity={
            "active_agent": _resolve_active_agent_name(context),
            "persona": active_persona.name,
            "persona_description": active_persona.description,
            "task_profile": str(getattr(task, "task_profile", "") or "chat"),
            "allow_write": active_persona.allow_write,
            "allow_spawn_subagent": active_persona.allow_spawn_subagent,
            "default_step_budget": active_persona.default_step_budget,
            "evidence_threshold": active_persona.evidence_threshold,
        },
        workspace={
            "cwd": current_cwd,
            "workspace_root": workspace_root,
            "sandbox_mode": sandbox.mode,
            "writable_roots": [str(path) for path in sandbox.writable_roots],
            "allow_network": sandbox.allow_network,
        },
        permission_snapshot={
            "persona": active_persona.name,
            "tool_access": active_persona.tool_access,
            "allow_write": active_persona.allow_write,
            "writable_roots": [str(path) for path in sandbox.writable_roots],
        },
        loaded_instructions=_resolve_loaded_instructions(
            application_context,
            cwd=current_cwd,
            workspace_root=workspace_root,
            target_paths=target_paths,
        ),
        recent_turns=recent_turns,
        focused_resources=focused_resources,
        recent_completed_actions=_recent_completed_actions(task),
        current_user_message=str(user_input or getattr(task, "goal", "") or "").strip(),
        pending_clarification=_pending_clarification(application_context),
        pending_verification=list(getattr(getattr(task, "state", None), "pending_verifications", []) or []),
        current_plan_state=_plan_state(task),
        memory_snapshot=_build_memory_snapshot(application_context, task=task, user_input=user_input),
        available_tools=summarize_available_tools(context, persona=active_persona),
    )


def build_run_context_block(
    context: Any,
    *,
    task: Any | None = None,
    session: Any | None = None,
    user_input: str = "",
    persona: RuntimePersona | None = None,
) -> str:
    snapshot = build_run_context(context, task=task, session=session, user_input=user_input, persona=persona)
    tool_lines = [
        f"- {tool.name}: access={tool.access}; permission={tool.permission_level or '(unknown)'}; risk={tool.risk_hint}; desc={tool.description}"
        for tool in snapshot.available_tools
    ] or ["- (none)"]
    recent_action_lines = [f"- {item}" for item in snapshot.recent_completed_actions] or ["- (none)"]
    recent_turn_lines = [f"- {item}" for item in snapshot.recent_turns] or ["- (none)"]
    focused_lines = [f"- {item}" for item in snapshot.focused_resources] or ["- (none)"]
    instruction_lines = [f"- {item}" for item in snapshot.loaded_instructions] or ["- (none)"]
    memory_lines = [f"- {item}" for item in _format_memory_snapshot(snapshot.memory_snapshot)] or ["- (none)"]
    pending_verifications = snapshot.pending_verification or ["(none)"]
    plan_lines = [f"- {item}" for item in snapshot.current_plan_state.get("tasks", [])] or ["- (none)"]
    return "\n".join(
        [
            "Runtime context:",
            f"- active_agent: {snapshot.identity['active_agent']}",
            f"- persona_description: {snapshot.identity['persona_description']}",
            f"- task_profile: {snapshot.identity['task_profile']}",
            f"- default_step_budget: {snapshot.identity['default_step_budget']}",
            f"- evidence_threshold: {snapshot.identity['evidence_threshold']}",
            f"- allow_write: {str(bool(snapshot.identity['allow_write'])).lower()}",
            f"- allow_spawn_subagent: {str(bool(snapshot.identity['allow_spawn_subagent'])).lower()}",
            f"- cwd: {snapshot.workspace['cwd'] or '(unknown)'}",
            f"- workspace_root: {snapshot.workspace['workspace_root'] or '(unknown)'}",
            f"- sandbox_mode: {snapshot.workspace['sandbox_mode']}",
            f"- writable_roots: {', '.join(snapshot.workspace['writable_roots']) or '(none)'}",
            f"- pending_clarification: {snapshot.pending_clarification or '(none)'}",
            f"- pending_verification: {', '.join(pending_verifications)}",
            f"- current_user_message: {snapshot.current_user_message or '(none)'}",
            "Recent turns:",
            *recent_turn_lines,
            "loaded_instructions:",
            *instruction_lines,
            "Recent focused resources:",
            *focused_lines,
            "recent_completed_actions:",
            *recent_action_lines,
            "current_plan_state:",
            *plan_lines,
            "memory_snapshot:",
            *memory_lines,
            "available_tools:",
            *tool_lines,
        ]
    )


def summarize_available_tools(context: Any, *, persona: RuntimePersona | None = None) -> list[RunContextToolSummary]:
    application_context = getattr(context, "application_context", context)
    active_persona = persona or resolve_runtime_persona(context)
    results: list[RunContextToolSummary] = []
    for name in application_context.tools.names():
        tool = application_context.tools.get(name)
        if tool is None:
            continue
        access = tool_access_for_persona(active_persona, tool)
        if access == "deny":
            continue
        permission_level = str(getattr(tool, "permission_level", "") or "")
        results.append(
            RunContextToolSummary(
                name=name,
                access=access,
                permission_level=permission_level,
                description=str(getattr(tool, "description", "") or "").strip(),
                risk_hint=_risk_hint(permission_level),
            )
        )
    return results


def available_tool_names(context: Any, *, persona: RuntimePersona | None = None) -> list[str]:
    return [item.name for item in summarize_available_tools(context, persona=persona)]


def _build_memory_snapshot(application_context: Any, *, task: Any | None, user_input: str) -> dict[str, Any]:
    snapshot = application_context.session_memory.snapshot()
    memory = getattr(task, "state", None)
    recalled: list[str] = []
    search = getattr(getattr(application_context, "index_memory", None), "search", None)
    query = str(user_input or getattr(task, "goal", "") or "").strip()
    if callable(search) and query:
        try:
            recalled = [record.text for record in search(query, limit=3)]
        except Exception:
            recalled = []
    return {
        "session_last_summary": str(getattr(snapshot, "last_summary", "") or "").strip(),
        "known_facts": list(getattr(memory, "known_facts", []) or []),
        "open_questions": list(getattr(memory, "open_questions", []) or []),
        "read_paths": list(getattr(memory, "read_paths", []) or []),
        "modified_paths": list(getattr(memory, "modified_paths", []) or []),
        "claims": list(getattr(memory, "claims", []) or []),
        "recalled_memories": recalled,
    }


def _resolve_loaded_instructions(
    application_context: Any,
    *,
    cwd: str,
    workspace_root: str,
    target_paths: list[str] | None = None,
) -> list[str]:
    config = getattr(application_context, "config", {}) or {}
    values: list[str] = []
    configured = config.get("instructions")
    if isinstance(configured, list):
        values.extend(str(item).strip() for item in configured if str(item).strip())
    services = getattr(application_context, "services", {}) or {}
    loaded = services.get("loaded_instructions")
    if isinstance(loaded, list):
        values.extend(str(item).strip() for item in loaded if str(item).strip())
    get = getattr(getattr(application_context, "index_memory", None), "get", None)
    if callable(get):
        payload = get("loaded_instructions")
        if isinstance(payload, list):
            values.extend(str(item).strip() for item in payload if str(item).strip())
    values.extend(_discover_instruction_files(cwd=cwd, workspace_root=workspace_root))
    for target_path in target_paths or []:
        values.extend(_discover_instruction_files(cwd=target_path, workspace_root=workspace_root))
    return list(dict.fromkeys(item for item in values if item))[:8]


def _pending_clarification(application_context: Any) -> str:
    get = getattr(getattr(application_context, "index_memory", None), "get", None)
    if not callable(get):
        return ""
    payload = get(_PENDING_CLARIFICATION_KEY)
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("message") or payload.get("goal") or "").strip()


def _recent_completed_actions(task: Any | None) -> list[str]:
    if task is None:
        return []
    result: list[str] = []
    for action in getattr(task, "actions", [])[-5:]:
        if getattr(action, "status", "") != "completed":
            continue
        tool_name = str(getattr(action, "metadata", {}).get("tool_name") or getattr(action, "kind", "") or "")
        observation = str(getattr(action, "observation", "") or "").strip().replace("\n", " ")
        if len(observation) > 160:
            observation = observation[:157].rstrip() + "..."
        result.append(f"{tool_name}: {observation or '(no observation)'}")
    return result


def _plan_state(task: Any | None) -> dict[str, Any]:
    plan = getattr(task, "plan", None)
    if plan is None:
        return {"status": "none", "tasks": []}
    tasks = [f"{item.title} [{item.status}] kind={item.kind}" for item in getattr(plan, "tasks", [])[:8]]
    return {
        "status": str(getattr(plan, "status", "pending") or "pending"),
        "workflow": str(getattr(plan, "metadata", {}).get("workflow") or ""),
        "tasks": tasks,
    }


def _format_resource(ref: Any) -> str:
    location = str(getattr(ref, "location", "") or "")
    if not location:
        return "(unknown)"
    try:
        label = Path(location).name or location
    except Exception:
        label = location
    return f"{label}: {location}"


def _format_memory_snapshot(snapshot: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in ("session_last_summary", "known_facts", "open_questions", "read_paths", "modified_paths", "claims", "recalled_memories"):
        value = snapshot.get(key)
        if isinstance(value, list):
            if value:
                lines.append(f"{key}={'; '.join(str(item) for item in value[:4])}")
            continue
        text = str(value or "").strip()
        if text:
            lines.append(f"{key}={text}")
    return lines


def _recent_turns(session: Any | None) -> list[str]:
    if session is None:
        return []
    result: list[str] = []
    for turn in getattr(session, "turns", [])[-6:]:
        role = str(getattr(turn, "role", "") or "unknown")
        content = str(getattr(turn, "content", "") or "").strip().replace("\n", " ")
        if len(content) > 300:
            content = content[:297].rstrip() + "..."
        result.append(f"{role}: {content or '(empty)'}")
    return result


def _risk_hint(permission_level: str) -> str:
    return {
        "metadata_read": "low",
        "content_read": "low",
        "safe_write": "high",
        "destructive_write": "destructive",
    }.get(permission_level, "low")


def _resolve_active_agent_name(context: Any) -> str:
    services = getattr(context, "services", {}) or {}
    active = str(services.get("active_agent") or "").strip()
    if active:
        return active
    application_context = getattr(context, "application_context", context)
    app_services = getattr(application_context, "services", {}) or {}
    active = str(app_services.get("active_agent") or "").strip()
    if active:
        return active
    config = getattr(application_context, "config", {}) or {}
    active = str(config.get("active_agent") or "").strip()
    return active or "codex"


def _discover_instruction_files(*, cwd: str, workspace_root: str) -> list[str]:
    roots: list[Path] = []
    for raw in (cwd, workspace_root):
        text = str(raw or "").strip()
        if not text:
            continue
        candidate = Path(text).expanduser().resolve()
        if candidate not in roots:
            roots.append(candidate)
    if not roots:
        return []
    workspace_path = roots[-1]
    current = roots[0]
    discovered: list[str] = []
    for directory in [current, *current.parents]:
        try:
            relative = directory == workspace_path or workspace_path in directory.parents or directory in workspace_path.parents
        except Exception:
            relative = False
        if not relative and directory != workspace_path:
            continue
        for filename in ("AGENTS.md", "CLAUDE.md"):
            path = directory / filename
            if path.exists():
                discovered.append(str(path))
        if directory == workspace_path:
            break
    memory_path = workspace_path / "MEMORY.md"
    if memory_path.exists():
        discovered.append(str(memory_path))
    return discovered


def update_loaded_instructions(context: Any, *paths: str) -> list[str]:
    application_context = getattr(context, "application_context", context)
    config = getattr(application_context, "config", {}) or {}
    cwd = str(config.get("default_directory") or "").strip()
    workspace_root = cwd
    try:
        workspace_root = str(resolve_sandbox(context).normalized_workspace_root())
    except Exception:
        workspace_root = cwd
    discovered: list[str] = []
    for path in paths:
        text = str(path or "").strip()
        if not text:
            continue
        discovered.extend(_discover_instruction_files(cwd=text, workspace_root=workspace_root))
    discovered = list(dict.fromkeys(item for item in discovered if item))
    if not discovered:
        return _resolve_loaded_instructions(application_context, cwd=cwd, workspace_root=workspace_root)
    services = getattr(application_context, "services", {}) or {}
    existing = services.get("loaded_instructions")
    merged = list(existing) if isinstance(existing, list) else []
    merged.extend(discovered)
    merged = list(dict.fromkeys(str(item).strip() for item in merged if str(item).strip()))
    services["loaded_instructions"] = merged[:16]
    put = getattr(getattr(application_context, "index_memory", None), "put", None)
    if callable(put):
        put("loaded_instructions", services["loaded_instructions"])
    return list(services["loaded_instructions"])


def _candidate_instruction_paths(task: Any | None, session_snapshot: Any) -> list[str]:
    candidates: list[str] = []
    semantics = getattr(getattr(task, "plan", None), "target_semantics", None)
    path = str(getattr(semantics, "path", "") or "").strip()
    if path:
        candidates.append(path)
    if task is not None:
        for action in reversed(getattr(task, "actions", [])):
            metadata = dict(getattr(action, "metadata", {}) or {})
            arguments = dict(metadata.get("arguments") or {})
            for key in ("path", "destination_path"):
                value = str(arguments.get(key) or "").strip()
                if value:
                    candidates.append(value)
            tool_output = dict(dict(metadata.get("result") or {}).get("tool_output") or {})
            for key in ("resolved_path", "path"):
                value = str(tool_output.get(key) or "").strip()
                if value:
                    candidates.append(value)
    for ref in getattr(session_snapshot, "focused_resources", [])[:5]:
        location = str(getattr(ref, "location", "") or "").strip()
        if location:
            candidates.append(location)
    return list(dict.fromkeys(item for item in candidates if item))
