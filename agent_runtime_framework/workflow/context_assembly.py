from __future__ import annotations

from typing import Any

from agent_runtime_framework.policy import SimpleDesktopPolicy


def _resource_payload(resource: Any) -> dict[str, Any]:
    return {
        "resource_id": getattr(resource, "resource_id", None),
        "kind": getattr(resource, "kind", None),
        "location": getattr(resource, "location", None),
        "title": getattr(resource, "title", None),
    }


def _memory_payload(application_context: Any | None) -> dict[str, Any]:
    if application_context is None:
        return {}
    session_memory = getattr(application_context, "session_memory", None)
    if session_memory is None or not hasattr(session_memory, "snapshot"):
        return {}
    snapshot = session_memory.snapshot()
    focused_resources = list(getattr(snapshot, "focused_resources", []) or [])
    return {
        "focused_resource": _resource_payload(focused_resources[0]) if focused_resources else None,
        "recent_resources": [_resource_payload(resource) for resource in focused_resources[:5]],
        "focused_resources": [str(getattr(resource, "location", resource)) for resource in focused_resources],
        "last_summary": getattr(snapshot, "last_summary", None),
    }


def _policy_context(application_context: Any | None) -> dict[str, Any]:
    policy = getattr(application_context, "policy", None)
    if policy is None:
        policy = SimpleDesktopPolicy()
    if policy is None:
        return {}
    return {
        "policy_name": type(policy).__name__,
    }


def build_runtime_context(*, application_context: Any, workspace_context: Any) -> dict[str, Any]:
    session_snapshot = application_context.session_memory.snapshot() if hasattr(application_context.session_memory, "snapshot") else None
    return {
        "application_context": application_context,
        "workspace_context": workspace_context,
        "memory": _memory_payload(application_context),
        "session_memory_snapshot": session_snapshot,
        "policy_context": _policy_context(application_context),
    }
