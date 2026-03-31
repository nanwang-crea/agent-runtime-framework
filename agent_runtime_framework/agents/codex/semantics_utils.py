from __future__ import annotations

from pathlib import Path
import re


_PROMPTS_DIR = Path(__file__).with_name("prompts")


def extract_json_block(text: str) -> str:
    stripped = str(text or "").strip()
    if "```" in stripped:
        stripped = re.sub(r"^.*?```(?:json)?\s*", "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"\s*```.*$", "", stripped, flags=re.DOTALL)
    return stripped.strip()


def recent_turns_block(session: object | None) -> str:
    turns = list(getattr(session, "turns", [])[-4:]) if session is not None else []
    if not turns:
        return "(none)"
    lines: list[str] = []
    for turn in turns:
        role = str(getattr(turn, "role", "") or "")
        content = " ".join(str(getattr(turn, "content", "") or "").split())
        lines.append(f"- {role}: {content[:180]}")
    return "\n".join(lines)


def workspace_candidates_block(workspace_root: Path | None) -> str:
    if workspace_root is None or not workspace_root.exists():
        return "(unknown)"
    entries = sorted(path.name for path in workspace_root.iterdir())[:20]
    return "\n".join(f"- {entry}" for entry in entries) if entries else "(empty)"


def workspace_root_from_context(context: object | None) -> Path | None:
    if context is None:
        return None
    application_context = getattr(context, "application_context", context)
    root_value = getattr(application_context, "config", {}).get("default_directory")
    return Path(str(root_value)).expanduser().resolve() if root_value else None


def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
