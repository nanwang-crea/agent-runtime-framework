from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterable

from agent_runtime_framework.models.core import AuthSession, CredentialStore, ModelProfile


class _CodexChatCompletions:
    def __init__(self, *, codex_binary: str, timeout_seconds: int) -> None:
        self._codex_binary = codex_binary
        self._timeout_seconds = timeout_seconds

    def create(self, **kwargs):
        model = str(kwargs.get("model") or "").strip()
        messages = kwargs.get("messages") or []
        if not model:
            raise RuntimeError("missing model for codex_local request")
        prompt = _build_prompt(messages)
        if kwargs.get("stream"):
            return self._create_stream(model=model, prompt=prompt)
        return self._create_once(model=model, prompt=prompt)

    def _create_once(self, *, model: str, prompt: str):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as file_obj:
            output_file = Path(file_obj.name)
        cmd = [
            self._codex_binary,
            "exec",
            "--skip-git-repo-check",
            "--model",
            model,
            "--output-last-message",
            str(output_file),
            prompt,
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(_format_process_error(completed.stderr, completed.stdout))
            content = output_file.read_text(encoding="utf-8").strip()
            return type(
                "ChatCompletionResponse",
                (),
                {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]},
            )()
        finally:
            output_file.unlink(missing_ok=True)

    def _create_stream(self, *, model: str, prompt: str) -> Iterable[Any]:
        cmd = [
            self._codex_binary,
            "exec",
            "--skip-git-repo-check",
            "--model",
            model,
            "--json",
            prompt,
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if process.stdout is None:
            raise RuntimeError("codex_local stream failed: no stdout")

        log_lines: list[str] = []
        seen_delta = False
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            event = _parse_json_line(line)
            if event is None:
                log_lines.append(line)
                continue
            text = _extract_event_text(event, allow_non_delta=not seen_delta)
            if text:
                event_type = str(event.get("type") or "")
                if "delta" in event_type:
                    seen_delta = True
                yield type(
                    "ChatCompletionChunk",
                    (),
                    {
                        "choices": [
                            type(
                                "Choice",
                                (),
                                {"delta": type("Delta", (), {"content": text})()},
                            )()
                        ]
                    },
                )()
        process.wait(timeout=self._timeout_seconds)
        if process.returncode != 0:
            raise RuntimeError(_format_process_error("\n".join(log_lines), ""))


class _CodexChat:
    def __init__(self, *, codex_binary: str, timeout_seconds: int) -> None:
        self.completions = _CodexChatCompletions(codex_binary=codex_binary, timeout_seconds=timeout_seconds)


class _CodexClient:
    def __init__(self, *, codex_binary: str, timeout_seconds: int) -> None:
        self.chat = _CodexChat(codex_binary=codex_binary, timeout_seconds=timeout_seconds)


@dataclass(slots=True)
class CodexLocalProvider:
    provider_name: str = "codex_local"
    codex_binary: str = "codex"
    auth_file: Path = field(default_factory=lambda: Path.home() / ".codex" / "auth.json")
    timeout_seconds: int = 120
    available_models: list[ModelProfile] = field(
        default_factory=lambda: [
            ModelProfile(
                provider="codex_local",
                model_name="gpt-5.3-codex",
                display_name="GPT-5.3 Codex",
                cost_level="medium",
                latency_level="medium",
                reasoning_level="high",
                recommended_roles=["conversation", "capability_selector", "planner"],
            ),
            ModelProfile(
                provider="codex_local",
                model_name="gpt-5.4",
                display_name="GPT-5.4",
                cost_level="high",
                latency_level="medium",
                reasoning_level="high",
                recommended_roles=["planner", "conversation"],
            ),
            ModelProfile(
                provider="codex_local",
                model_name="gpt-5.4-mini",
                display_name="GPT-5.4 Mini",
                cost_level="low",
                latency_level="low",
                reasoning_level="medium",
                recommended_roles=["conversation", "capability_selector"],
            ),
        ]
    )

    def list_models(self) -> list[ModelProfile]:
        return [ModelProfile(**{**item.as_dict(), "provider": self.provider_name}) for item in self.available_models]

    def authenticate(self, credentials: dict[str, Any], store: CredentialStore) -> AuthSession:
        codex_binary = str(credentials.get("codex_binary") or self.codex_binary).strip() or self.codex_binary
        auth_file = Path(str(credentials.get("auth_file") or self.auth_file)).expanduser()
        if shutil.which(codex_binary) is None:
            return AuthSession(
                provider=self.provider_name,
                authenticated=False,
                auth_type="local_session",
                error_message="codex_cli_not_found",
            )
        auth_payload = _read_auth_payload(auth_file)
        if auth_payload is None:
            return AuthSession(
                provider=self.provider_name,
                authenticated=False,
                auth_type="local_session",
                error_message="codex_auth_missing",
            )
        store.set(
            self.provider_name,
            {
                "codex_binary": codex_binary,
                "auth_file": str(auth_file),
            },
        )
        return AuthSession(
            provider=self.provider_name,
            authenticated=True,
            auth_type="local_session",
            metadata={"auth_mode": str(auth_payload.get("auth_mode") or "")},
        )

    def get_client(self, store: CredentialStore) -> Any:
        stored = store.get(self.provider_name) or {}
        codex_binary = str(stored.get("codex_binary") or self.codex_binary).strip() or self.codex_binary
        auth_file = Path(str(stored.get("auth_file") or self.auth_file)).expanduser()
        if shutil.which(codex_binary) is None:
            return None
        if _read_auth_payload(auth_file) is None:
            return None
        return _CodexClient(codex_binary=codex_binary, timeout_seconds=self.timeout_seconds)


def _build_prompt(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return "请直接给出简洁回复。"
    rendered: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").strip().upper()
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        rendered.append(f"{role}:\n{content}")
    rendered.append("ASSISTANT:")
    return "\n\n".join(rendered)


def _read_auth_payload(auth_file: Path) -> dict[str, Any] | None:
    if not auth_file.exists():
        return None
    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    if not str(tokens.get("access_token") or "").strip() and not str(tokens.get("refresh_token") or "").strip():
        return None
    return payload


def _parse_json_line(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_event_text(event: dict[str, Any], *, allow_non_delta: bool) -> str:
    event_type = str(event.get("type") or "")
    if "delta" in event_type:
        delta = event.get("delta")
        if isinstance(delta, str) and delta:
            return delta
        if isinstance(delta, dict):
            value = delta.get("text") or delta.get("content")
            if isinstance(value, str) and value:
                return value
    if not allow_non_delta:
        return ""
    for key in ("content", "text"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content:
            return content
    return ""


def _format_process_error(stderr_text: str, stdout_text: str) -> str:
    candidate = (stderr_text or stdout_text or "").strip()
    if not candidate:
        return "codex_local request failed"
    compact = " ".join(candidate.split())
    return compact[:300]
