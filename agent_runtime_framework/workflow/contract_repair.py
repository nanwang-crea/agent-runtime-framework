from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.llm_access import chat_json


def build_contract_repair_system_prompt(*, contract_kind: str, required_fields: list[str], extra_instructions: str = "") -> str:
    required = ", ".join(required_fields)
    extra = f" {extra_instructions.strip()}" if extra_instructions.strip() else ""
    return (
        f"You repair an invalid structured workflow contract for {contract_kind}. "
        "Return JSON only. Preserve valid fields when possible, but fix missing or invalid required fields. "
        f"The repaired contract must include: {required}.{extra}"
    )


def repair_structured_output(
    context: Any,
    *,
    role: str,
    contract_kind: str,
    required_fields: list[str],
    original_output: Any,
    validation_error: str,
    request_payload: dict[str, Any],
    extra_instructions: str = "",
) -> dict[str, Any] | None:
    payload = {
        "contract_kind": contract_kind,
        "validation_error": validation_error,
        "original_output": original_output,
        "request_payload": request_payload,
    }
    return chat_json(
        context,
        role=role,
        system_prompt=build_contract_repair_system_prompt(
            contract_kind=contract_kind,
            required_fields=required_fields,
            extra_instructions=extra_instructions,
        ),
        payload=payload,
        max_tokens=500,
        temperature=0.0,
    )
