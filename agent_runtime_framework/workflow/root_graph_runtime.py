from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_runtime_framework.workflow.models import WorkflowGraph, WorkflowNode


AnalyzeGoalFn = Callable[[str, Any], Any]
MarkRouteFn = Callable[[str, str], None]
HasPendingClarificationFn = Callable[[], bool]
RunConversationFn = Callable[[str, WorkflowGraph, dict[str, Any]], dict[str, Any]]
RunAgentFn = Callable[[str, Any, dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class RootGraphRuntime:
    analyze_goal_fn: AnalyzeGoalFn
    context: Any
    mark_route_decision: MarkRouteFn
    has_pending_clarification: HasPendingClarificationFn
    run_conversation: RunConversationFn
    run_agent: RunAgentFn

    def run(self, message: str) -> dict[str, Any]:
        goal = self._analyze_goal(message)
        route = "conversation" if self._is_conversation_goal(goal) else "agent"
        root_graph = {"route": route, "intent": str(getattr(goal, "primary_intent", "") or "")}
        if route == "conversation":
            payload = self.run_conversation(message, self._build_conversation_graph(goal), root_graph)
        else:
            payload = self.run_agent(message, goal, root_graph)
        return self._with_root_trace(payload, goal, route)

    def _analyze_goal(self, message: str) -> Any:
        route_source = "clarification" if self.has_pending_clarification() else "goal_analysis"
        self.mark_route_decision("workflow", route_source)
        return self.analyze_goal_fn(message, self.context)

    def _is_conversation_goal(self, goal: Any) -> bool:
        if self.has_pending_clarification():
            return False
        return str(getattr(goal, "primary_intent", "") or "").strip() in {"generic", "chat", "conversation"}

    def _build_conversation_graph(self, goal: Any) -> WorkflowGraph:
        return WorkflowGraph(
            nodes=[WorkflowNode(node_id="final_response", node_type="final_response", metadata={"conversation_mode": True})],
            edges=[],
            metadata={
                "goal": getattr(goal, "original_goal", ""),
                "source": "conversation_graph",
                "execution_mode": "native",
                "conversation_mode": True,
            },
        )

    def _with_root_trace(self, payload: dict[str, Any], goal: Any, route: str) -> dict[str, Any]:
        updated = dict(payload)
        trace = list(updated.get("execution_trace") or [])
        root_steps = [
            {"name": "goal_intake", "status": "completed", "detail": "goal_intake"},
            {"name": "route_by_goal", "status": "completed", "detail": f"route={route}; intent={getattr(goal, 'primary_intent', '')}"},
        ]
        existing_names = {str(step.get("name") or "") for step in trace if isinstance(step, dict)}
        prefixed = [step for step in root_steps if step["name"] not in existing_names]
        if trace and isinstance(trace[0], dict) and str(trace[0].get("name") or "") == "router":
            updated["execution_trace"] = [trace[0], *prefixed, *trace[1:]]
        else:
            updated["execution_trace"] = [*prefixed, *trace]
        updated["root_graph"] = {
            "route": route,
            "intent": str(getattr(goal, "primary_intent", "") or ""),
        }
        return updated
