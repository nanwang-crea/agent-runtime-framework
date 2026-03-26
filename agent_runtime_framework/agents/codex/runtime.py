from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CodexSessionRuntime:
    max_observation_chars: int = 1200
    max_summary_entries: int = 6
    events: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, event_type: str, **payload: Any) -> None:
        self.events.append({"type": event_type, **payload})

    def on_task_started(self, task: Any) -> None:
        self.emit(
            "task_started",
            goal=getattr(task, "goal", ""),
            task_profile=getattr(task, "task_profile", "chat"),
            runtime_persona=getattr(task, "runtime_persona", "general"),
        )

    def before_tool_call(self, tool: Any, call: Any, task: Any) -> Any:
        self.emit(
            "tool_call",
            tool_name=getattr(tool, "name", ""),
            arguments=dict(getattr(call, "arguments", {}) or {}),
            task_goal=getattr(task, "goal", ""),
        )
        return call

    def after_tool_call(self, tool: Any, call: Any, result: Any, task: Any) -> Any:
        self.emit(
            "tool_result",
            tool_name=getattr(tool, "name", ""),
            success=bool(getattr(result, "success", False)),
            task_goal=getattr(task, "goal", ""),
        )
        return result

    def compact_text(self, text: str) -> str:
        stripped = text.strip()
        if len(stripped) <= self.max_observation_chars:
            return stripped
        return f"{stripped[: self.max_observation_chars].rstrip()} ...[summary truncated]"

    def record_action(self, task: Any, action: Any) -> None:
        self.emit(
            "action_completed",
            kind=getattr(action, "kind", ""),
            status=getattr(action, "status", ""),
        )
        task.summary = self.build_task_summary(task)

    def build_task_summary(self, task: Any) -> str:
        entries: list[str] = []
        for action in getattr(task, "actions", [])[-self.max_summary_entries :]:
            if getattr(action, "status", "") != "completed":
                continue
            tool_name = str(getattr(action, "metadata", {}).get("tool_name") or action.kind)
            observation = self.compact_text(str(getattr(action, "observation", "") or ""))
            entries.append(f"- {tool_name}: {observation}")
        return "\n".join(entries)
