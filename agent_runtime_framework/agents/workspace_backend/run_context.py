from __future__ import annotations

from typing import Any


def available_tool_names(context: Any) -> list[str]:
    application_context = getattr(context, "application_context", None)
    if application_context is None or not hasattr(application_context, "tools"):
        return []
    return list(application_context.tools.names())


def build_run_context_block(context: Any, *, session: Any | None = None, user_input: str = "") -> str:
    application_context = getattr(context, "application_context", context)
    config = getattr(application_context, "config", {}) or {}
    workspace = str(config.get("default_directory") or "")
    tool_names = available_tool_names(context)
    focused_lines: list[str] = []
    if session is not None:
        snapshot = getattr(application_context, "session_memory", None)
        if snapshot is not None and hasattr(snapshot, "snapshot"):
            state = snapshot.snapshot()
            for ref in getattr(state, "focused_resources", [])[:5]:
                focused_lines.append(f"- {getattr(ref, 'location', '')}")
    focused_block = "\n".join(focused_lines) if focused_lines else "- (none)"
    user_line = f"User input: {user_input}\n" if user_input else ""
    return (
        f"Workspace: {workspace or '(unknown)'}\n"
        f"{user_line}"
        f"Focused resources:\n{focused_block}\n"
        f"Available tools: {', '.join(tool_names) if tool_names else '(none)'}"
    )


def update_loaded_instructions(context: Any, instruction: str) -> None:
    services = getattr(context, "services", None)
    if isinstance(services, dict):
        services["loaded_instructions"] = str(instruction or "")
