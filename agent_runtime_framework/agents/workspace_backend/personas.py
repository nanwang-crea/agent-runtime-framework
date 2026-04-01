from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuntimePersona:
    name: str
    description: str
    prompt_preamble: str
    allow_write: bool = True
    tool_access: str = "allow"
    ask_tool_names: tuple[str, ...] = ()
    deny_tool_names: tuple[str, ...] = ()
    ask_permission_levels: tuple[str, ...] = ()
    deny_permission_levels: tuple[str, ...] = ()
    allow_permission_levels: tuple[str, ...] = ()
    allow_tool_names: tuple[str, ...] = ()
    default_step_budget: int = 8
    evidence_threshold: str = "medium"
    task_profiles: tuple[str, ...] = field(default_factory=tuple)


_PERSONAS = {
    "general": RuntimePersona(
        name="general",
        description="General workspace persona.",
        prompt_preamble="You are a general workspace assistant.",
        allow_write=True,
    ),
    "explore": RuntimePersona(
        name="explore",
        description="Read and explain workspace contents.",
        prompt_preamble="You are an exploration persona. Prefer reading and summarizing.",
        allow_write=False,
        deny_permission_levels=("safe_write", "destructive_write"),
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
    profile = str(getattr(task, "task_profile", "") or "").strip().lower()
    if profile in {"repository_explainer", "file_reader"}:
        return require_runtime_persona("explore")
    return require_runtime_persona("general")


def tool_access_for_persona(persona: RuntimePersona, tool: Any) -> str:
    tool_name = str(getattr(tool, "name", "") or "")
    permission_level = str(getattr(tool, "permission_level", "") or "")
    if tool_name in persona.deny_tool_names or permission_level in persona.deny_permission_levels:
        return "deny"
    if tool_name in persona.ask_tool_names or permission_level in persona.ask_permission_levels:
        return "ask"
    if persona.tool_access == "deny":
        return "deny"
    if not persona.allow_write and permission_level in {"safe_write", "destructive_write"}:
        return "deny"
    return "allow"
