from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent_runtime_framework.artifacts import InMemoryArtifactStore
from agent_runtime_framework.core.models import Observation, RunResult, StepRecord
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory, WorkingMemory
from agent_runtime_framework.observability import InMemoryRunObserver, RunEvent, RunObserver
from agent_runtime_framework.policy import PolicyDecision
from agent_runtime_framework.resources import LocalResourceResolver, ResourceRef
from agent_runtime_framework.tools.registry import ToolRegistry


class Interpreter(Protocol):
    def __call__(self, user_input: str, context: "ApplicationContext") -> Any: ...


class Resolver(Protocol):
    def __call__(self, intent: Any, context: "ApplicationContext") -> list[ResourceRef]: ...


class Planner(Protocol):
    def __call__(self, intent: Any, resources: list[ResourceRef], context: "ApplicationContext") -> list[Any]: ...


class Authorizer(Protocol):
    def __call__(self, action: Any, context: "ApplicationContext", *, confirmed: bool) -> PolicyDecision: ...


class Executor(Protocol):
    def __call__(self, action: Any, context: "ApplicationContext", working_memory: WorkingMemory) -> Any: ...


class Composer(Protocol):
    def __call__(self, outcome: Any, context: "ApplicationContext") -> tuple[str, list[Observation]]: ...


class Rememberer(Protocol):
    def __call__(self, outcome: Any, context: "ApplicationContext") -> None: ...


class Rollbacker(Protocol):
    def __call__(
        self,
        completed_outcomes: list[Any],
        context: "ApplicationContext",
        working_memory: WorkingMemory,
        *,
        cause: Exception,
    ) -> dict[str, Any] | None: ...


@dataclass(slots=True)
class ApplicationSpec:
    name: str
    interpreter: Interpreter
    resolver: Resolver
    planner: Planner
    authorizer: Authorizer
    executor: Executor
    composer: Composer
    rememberer: Rememberer
    rollbacker: Rollbacker | None = None


@dataclass(slots=True)
class ApplicationContext:
    resource_repository: Any
    session_memory: Any = field(default_factory=InMemorySessionMemory)
    index_memory: Any = field(default_factory=InMemoryIndexMemory)
    artifact_store: Any = field(default_factory=InMemoryArtifactStore)
    policy: Any = None
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    config: dict[str, Any] = field(default_factory=dict)
    llm_client: Any = None
    llm_model: str = "gpt-5.4"
    services: dict[str, Any] = field(default_factory=dict)
    resource_resolver: Any = field(default_factory=LocalResourceResolver)
    observer: RunObserver = field(default_factory=InMemoryRunObserver)
    working_memory_factory: Callable[[], WorkingMemory] = WorkingMemory


class ApplicationRunner:
    def __init__(self, spec: ApplicationSpec, context: ApplicationContext) -> None:
        self.spec = spec
        self.context = context

    def run(self, user_input: str, *, confirmed: bool = False) -> RunResult:
        working_memory = self.context.working_memory_factory()
        steps: list[StepRecord] = []
        outcome = None
        completed_outcomes: list[Any] = []

        intent = self.spec.interpreter(user_input, self.context)
        self._record("interpret", user_input)
        steps.append(StepRecord(name="interpret", status="completed"))

        resources = self.spec.resolver(intent, self.context)
        working_memory.set("resolved_resources", resources)
        self._record("resolve", f"resolved {len(resources)} resources")
        steps.append(StepRecord(name="resolve", status="completed"))

        actions = self.spec.planner(intent, resources, self.context)
        working_memory.set("actions", actions)
        self._record("plan", f"planned {len(actions)} actions")
        steps.append(StepRecord(name="plan", status="completed"))

        for action in actions:
            decision = self.spec.authorizer(action, self.context, confirmed=confirmed)
            self._record("authorize", decision.reason, {"allowed": decision.allowed})
            steps.append(StepRecord(name="authorize", status="completed", detail=decision.reason))
            if not decision.allowed:
                working_memory.clear()
                return RunResult(
                    status="blocked",
                    final_answer=decision.reason,
                    steps=steps,
                    termination_reason=decision.reason,
                )
            if decision.requires_confirmation:
                working_memory.clear()
                return RunResult(
                    status="requires_confirmation",
                    final_answer=decision.safe_alternative or decision.reason,
                    steps=steps,
                    termination_reason=decision.reason,
                )
            try:
                outcome = self.spec.executor(action, self.context, working_memory)
                completed_outcomes.append(outcome)
                self._record("execute", getattr(action, "name", "action"))
                steps.append(StepRecord(name="execute", status="completed"))
            except Exception as exc:
                rollback_summary: dict[str, Any] = {}
                if callable(self.spec.rollbacker) and completed_outcomes:
                    rollback_summary = dict(
                        self.spec.rollbacker(completed_outcomes, self.context, working_memory, cause=exc) or {}
                    )
                self._record(
                    "rollback",
                    "rolled back completed actions after failure",
                    {"cause": f"{type(exc).__name__}: {exc}", **rollback_summary},
                )
                steps.append(StepRecord(name="rollback", status="completed"))
                working_memory.clear()
                raise

        if outcome is None:
            outcome = {"text": "没有可执行动作。", "focused_resources": []}

        final_answer, observations = self.spec.composer(outcome, self.context)
        self._record("compose", "final answer ready")
        steps.append(StepRecord(name="compose", status="completed"))

        self.spec.rememberer(outcome, self.context)
        self._record("remember", "session updated")
        steps.append(StepRecord(name="remember", status="completed"))
        working_memory.clear()

        return RunResult(
            status="completed",
            final_answer=final_answer,
            steps=steps,
            observations=observations,
        )

    def _record(self, stage: str, detail: str, payload: dict[str, Any] | None = None) -> None:
        self.context.observer.record(RunEvent(stage=stage, detail=detail, payload=payload or {}))
