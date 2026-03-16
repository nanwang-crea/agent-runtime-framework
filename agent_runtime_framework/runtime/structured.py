from __future__ import annotations

import json
import re
from typing import Any, Callable


def _extract_json_block(text: str) -> str:
    stripped = text.strip()
    if "```" in stripped:
        stripped = re.sub(r"^.*?```(?:json)?\s*", "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"\s*```.*$", "", stripped, flags=re.DOTALL)
    return stripped.strip()


def parse_structured_output(
    llm_client: Any,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    normalizer: Callable[[dict[str, Any]], Any],
    temperature: float = 0.0,
    max_tokens: int = 400,
) -> Any | None:
    if llm_client is None or not hasattr(llm_client, "chat"):
        return None

    response = llm_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    raw_content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(_extract_json_block(raw_content))
    except Exception:
        return None
    try:
        return normalizer(parsed)
    except Exception:
        return None
