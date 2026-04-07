from __future__ import annotations

from typing import Any


def record_run(
    *,
    payload: dict[str, Any],
    prompt: str,
    run_inputs: dict[str, str],
    run_history: list[dict[str, Any]],
    limit: int = 40,
) -> list[dict[str, Any]]:
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return list(run_history)
    entry = {
        "run_id": run_id,
        "status": str(payload.get("status") or ""),
        "prompt": prompt,
        "final_answer_preview": str(payload.get("final_answer") or "")[:160],
    }
    run_inputs[run_id] = prompt
    filtered = [item for item in run_history if item.get("run_id") != run_id]
    filtered.insert(0, entry)
    return filtered[:limit]
