from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimePersona:
    name: str
    description: str
    prompt_preamble: str
    tool_access: str = "allow"
    allow_write: bool = True
    default_step_budget: int = 8
    allow_spawn_subagent: bool = False
    evidence_threshold: str = "medium"
    allow_tool_names: tuple[str, ...] = ()
    ask_tool_names: tuple[str, ...] = ()
    deny_tool_names: tuple[str, ...] = ()
    allow_permission_levels: tuple[str, ...] = ()
    ask_permission_levels: tuple[str, ...] = ()
    deny_permission_levels: tuple[str, ...] = ()
    task_profiles: tuple[str, ...] = field(default_factory=tuple)


_PERSONAS: dict[str, RuntimePersona] = {
    "build": RuntimePersona(
        name="build",
        description="Default execution persona for editing, fixing, and verifying workspace changes.",
        prompt_preamble="You are in build persona. Focus on completing the user's request. Workspace edits are allowed; schedule verification after changes.",
        allow_write=True,
        default_step_budget=10,
        evidence_threshold="medium",
        task_profiles=("change_and_verify",),
    ),
    "plan": RuntimePersona(
        name="plan",
        description="Analysis-first persona. Focus on understanding and planning, not editing.",
        prompt_preamble="You are in plan persona. Focus on analysis, planning, and explanation. Do not modify the workspace unless explicitly instructed.",
        allow_write=False,
        default_step_budget=8,
        evidence_threshold="high",
        deny_permission_levels=("safe_write", "destructive_write"),
        ask_tool_names=("run_shell_command",),
    ),
    "explore": RuntimePersona(
        name="explore",
        description="Read-first persona for locating targets, reading files, and understanding repositories.",
        prompt_preamble="You are in explore persona. Focus on locating targets, reading content, and understanding structure. Do not modify the workspace.",
        allow_write=False,
        default_step_budget=8,
        evidence_threshold="high",
        allow_permission_levels=("metadata_read", "content_read"),
        ask_tool_names=("run_shell_command",),
        deny_permission_levels=("safe_write", "destructive_write"),
        task_profiles=("repository_explainer", "file_reader"),
    ),
    "general": RuntimePersona(
        name="general",
        description="General-purpose persona for mixed reasoning tasks and follow-up conversations.",
        prompt_preamble="You are in general persona. Switch between analysis, reading, and summarizing as needed. Only modify the workspace when explicitly requested.",
        allow_write=True,
        default_step_budget=8,
        evidence_threshold="medium",
        task_profiles=("chat",),
    ),
    "debug": RuntimePersona(
        name="debug",
        description="Debugging and root-cause analysis persona. Prioritizes evidence gathering before proposing fixes.",
        prompt_preamble="You are in debug persona. Gather error evidence and locate the root cause before proposing a fix. Do not guess without sufficient evidence.",
        allow_write=True,
        default_step_budget=12,
        evidence_threshold="high",
        ask_tool_names=("run_shell_command",),
        task_profiles=("debug_and_fix",),
    ),
    "multi_edit": RuntimePersona(
        name="multi_edit",
        description="Multi-file change persona. Plans all edits upfront and verifies after all changes are applied.",
        prompt_preamble="You are in multi_edit persona. List all files to modify and their reasons upfront, apply changes in dependency order, then run tests once all edits are complete.",
        allow_write=True,
        default_step_budget=16,
        evidence_threshold="high",
        task_profiles=("multi_file_change",),
    ),
    "test": RuntimePersona(
        name="test",
        description="Test execution and analysis persona. Runs tests, reads output, and iterates on failures.",
        prompt_preamble="You are in test persona. Run tests first, read failure output carefully, locate the root cause, fix it, then re-run until all tests pass.",
        allow_write=True,
        default_step_budget=12,
        evidence_threshold="medium",
        task_profiles=("test_and_verify",),
    ),
    "summary": RuntimePersona(
        name="summary",
        description="Compression and summarization persona for turning collected evidence into concise output.",
        prompt_preamble="You are in summary persona. Compress, organize, and synthesize existing evidence into concise output. Do not expand into new investigation areas.",
        tool_access="deny",
        allow_write=False,
        default_step_budget=4,
        evidence_threshold="low",
    ),
    "compaction": RuntimePersona(
        name="compaction",
        description="Context compaction persona for aggressively summarizing long history into durable state.",
        prompt_preamble="You are in compaction persona. Compress context and preserve only the key information needed to continue work.",
        tool_access="deny",
        allow_write=False,
        default_step_budget=4,
        evidence_threshold="low",
    ),
}


def list_runtime_personas() -> list[RuntimePersona]:
    return list(_PERSONAS.values())


def get_runtime_persona(name: str) -> RuntimePersona | None:
    return _PERSONAS.get(str(name or "").strip().lower())


def require_runtime_persona(name: str) -> RuntimePersona:
    persona = get_runtime_persona(name)
    if persona is None:
        raise KeyError(f"unknown runtime persona: {name}")
    return persona


def resolve_runtime_persona(context: Any | None, *, task: Any | None = None, user_input: str = "") -> RuntimePersona:
    explicit = _resolve_explicit_persona_name(context, task=task)
    if explicit:
        return require_runtime_persona(explicit)
    profile = str(getattr(task, "task_profile", "") or "").strip().lower()
    if profile == "change_and_verify":
        return require_runtime_persona("build")
    if profile in {"repository_explainer", "file_reader"}:
        return require_runtime_persona("explore")
    if profile == "debug_and_fix":
        return require_runtime_persona("debug")
    if profile == "multi_file_change":
        return require_runtime_persona("multi_edit")
    if profile == "test_and_verify":
        return require_runtime_persona("test")
    return require_runtime_persona("general")


def tool_access_for_persona(persona: RuntimePersona, tool: Any) -> str:
    tool_name = str(getattr(tool, "name", "") or "").strip()
    permission_level = str(getattr(tool, "permission_level", "") or "").strip()
    if tool_name in persona.deny_tool_names:
        return "deny"
    if tool_name in persona.ask_tool_names:
        return "ask"
    if persona.allow_tool_names and tool_name in persona.allow_tool_names:
        return "allow"
    if persona.tool_access == "deny":
        return "deny"
    if permission_level in persona.deny_permission_levels:
        return "deny"
    if permission_level in persona.ask_permission_levels:
        return "ask"
    if persona.allow_permission_levels:
        return "allow" if permission_level in persona.allow_permission_levels else "deny"
    if not persona.allow_write and permission_level in {"safe_write", "destructive_write"}:
        return "deny"
    return "allow"


def _resolve_explicit_persona_name(context: Any | None, *, task: Any | None = None) -> str:
    task_name = str(getattr(task, "runtime_persona", "") or "").strip().lower()
    if task_name:
        return task_name
    session = getattr(context, "session", None)
    session_name = str(getattr(session, "active_persona", "") or "").strip().lower()
    if session_name:
        return session_name
    services = getattr(context, "services", {}) or {}
    service_name = str(services.get("codex_runtime_persona") or services.get("active_persona") or "").strip().lower()
    if service_name:
        return service_name
    application_context = getattr(context, "application_context", context)
    config = getattr(application_context, "config", {}) or {}
    return str(config.get("codex_runtime_persona") or config.get("active_persona") or "").strip().lower()
