from __future__ import annotations

"""Structured JSON repair: loop LLM fix attempts until ``validate`` accepts the payload."""

import json
from collections.abc import Callable
from typing import Any

from agent_runtime_framework.models import ChatMessage, ChatRequest, chat_once, resolve_model_runtime
from agent_runtime_framework.workflow.llm.access import get_application_context
from agent_runtime_framework.workflow.planning.prompt_utils import extract_json_block

DEFAULT_REPAIR_ATTEMPTS = 3


def build_contract_repair_system_prompt(*, contract_kind: str, required_fields: list[str], extra_instructions: str = "") -> str:
    required = ", ".join(required_fields)
    extra = f" {extra_instructions.strip()}" if extra_instructions.strip() else ""
    return (
        f"You repair an invalid structured workflow contract for {contract_kind}. "
        "Return JSON only. Preserve valid fields when possible, but fix missing or invalid required fields. "
        f"The repaired contract must include: {required}.{extra}"
    )


def parse_json_object(raw_text: Any) -> tuple[dict[str, Any] | None, str | None]:
    text = str(raw_text or "").strip()
    if not text:
        return None, "empty response"
    try:
        parsed = json.loads(extract_json_block(text))
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(parsed, dict):
        return None, "parsed response is not an object"
    return parsed, None


def _repair_attempt(
    context: Any,
    *,
    role: str,
    system_prompt: str,
    payload: dict[str, Any],
    max_tokens: int = 500,
) -> tuple[dict[str, Any] | None, str | None]:
    application_context = get_application_context(context)
    if application_context is None:
        return None, "missing application context"
    runtime = resolve_model_runtime(application_context, role)
    llm_client = runtime.client if runtime is not None else getattr(application_context, "llm_client", None)
    model_name = runtime.profile.model_name if runtime is not None else getattr(application_context, "llm_model", "")
    if llm_client is None or not model_name:
        return None, "model unavailable"
    try:
        response = chat_once(
            llm_client,
            ChatRequest(
                model=model_name,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
                ],
                temperature=0.0,
                max_tokens=max_tokens,
            ),
        )
    except Exception as exc:
        return None, str(exc) or "repair model call failed"
    return parse_json_object(str(response.content or ""))


def _repair_structured_json_round(
    context: Any,
    *,
    role: str,
    contract_kind: str,
    required_fields: list[str],
    original_output: Any,
    validation_error: str,
    request_payload: dict[str, Any],
    extra_instructions: str,
    outer_attempt: int,
    outer_max: int,
) -> dict[str, Any] | None:
    system_prompt = build_contract_repair_system_prompt(
        contract_kind=contract_kind,
        required_fields=required_fields,
        extra_instructions=extra_instructions,
    )
    payload = {
        "contract_kind": contract_kind,
        "validation_error": validation_error,
        "original_output": original_output,
        "request_payload": request_payload,
        "attempt": outer_attempt,
        "max_attempts": outer_max,
    }
    repaired, _ = _repair_attempt(
        context,
        role=role,
        system_prompt=system_prompt,
        payload=payload,
    )
    return repaired if isinstance(repaired, dict) else None


def repair_structured_contract(
    context: Any,
    *,
    role: str,
    contract_kind: str,
    required_fields: list[str],
    original_output: Any,
    request_payload: dict[str, Any],
    validate: Callable[[Any], str | None],
    extra_instructions: str = "",
    max_attempts: int = DEFAULT_REPAIR_ATTEMPTS,
    on_record: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    """Ask the repair model up to ``max_attempts`` times until ``validate(candidate)`` returns ``None``."""

    latest_output = original_output
    latest_error = validate(original_output)
    if latest_error is None and isinstance(original_output, dict):
        return original_output

    initial_validation_error = latest_error
    for attempt in range(1, max_attempts + 1):
        repaired = _repair_structured_json_round(
            context,
            role=role,
            contract_kind=contract_kind,
            required_fields=required_fields,
            original_output=latest_output,
            validation_error=str(latest_error or "invalid structured output"),
            request_payload=request_payload,
            extra_instructions=extra_instructions,
            outer_attempt=attempt,
            outer_max=max_attempts,
        )
        if not isinstance(repaired, dict):
            latest_output = {"prior_repair_attempt": latest_output, "attempt": attempt}
            continue
        latest_output = repaired
        latest_error = validate(repaired)
        if latest_error is None:
            if callable(on_record):
                on_record(
                    {
                        "contract_kind": contract_kind,
                        "role": role,
                        "success": True,
                        "attempts_used": attempt,
                        "max_attempts": max_attempts,
                        "initial_error": initial_validation_error or "",
                        "final_error": "",
                    }
                )
            return repaired

    if callable(on_record):
        on_record(
            {
                "contract_kind": contract_kind,
                "role": role,
                "success": False,
                "attempts_used": max_attempts,
                "max_attempts": max_attempts,
                "initial_error": initial_validation_error or "",
                "final_error": str(latest_error or "invalid structured output"),
            }
        )
    return None
