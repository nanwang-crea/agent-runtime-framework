from __future__ import annotations

from typing import Any, Callable, TypeVar

from agent_runtime_framework.applications.core import ApplicationContext
from agent_runtime_framework.models import resolve_model_runtime
from agent_runtime_framework.runtime import parse_structured_output


ValueT = TypeVar("ValueT")


def run_stage_parser(
    *,
    context: ApplicationContext,
    service_name: str,
    service_args: tuple[Any, ...],
    llm_system_prompt: str | None,
    llm_user_prompt: str | None,
    normalizer: Callable[[Any], ValueT | None],
    fallback: Callable[[], ValueT],
    max_tokens: int = 300,
) -> ValueT:
    custom_parser = context.services.get(service_name)
    if callable(custom_parser):
        custom_value = normalizer(custom_parser(*service_args, context))
        if custom_value is not None:
            return custom_value

    if llm_system_prompt and llm_user_prompt:
        runtime = resolve_model_runtime(context, _role_for_stage(service_name))
        llm_value = parse_structured_output(
            runtime.client if runtime is not None else context.llm_client,
            model=runtime.profile.model_name if runtime is not None else context.llm_model,
            system_prompt=llm_system_prompt,
            user_prompt=llm_user_prompt,
            normalizer=normalizer,
            max_tokens=max_tokens,
        )
        if llm_value is not None:
            return llm_value

    return fallback()


def _role_for_stage(service_name: str) -> str:
    mapping = {
        "intent_parser": "interpreter",
        "resolver_parser": "resolver",
        "planner_parser": "planner",
        "executor_parser": "executor",
        "composer_parser": "composer",
    }
    return mapping.get(service_name, service_name)
