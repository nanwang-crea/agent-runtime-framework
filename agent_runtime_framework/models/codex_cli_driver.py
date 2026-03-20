from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterable

from agent_runtime_framework.models.chat import ChatChunk, ChatRequest, ChatResponse
from agent_runtime_framework.models.core import AuthSession, CredentialStore, DriverCapabilities, ModelDriver, ModelInstance, ModelProfile


class _CodexCliClient:
    def __init__(self, *, codex_binary: str, timeout_seconds: int) -> None:
        self._codex_binary = codex_binary
        self._timeout_seconds = timeout_seconds
        self.chat = _CodexCompatChat(self)

    def create_chat_completion(self, request: ChatRequest) -> ChatResponse:
        output_file: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as file_obj:
                output_file = Path(file_obj.name)
            completed = subprocess.run(
                [
                    self._codex_binary,
                    "exec",
                    "--skip-git-repo-check",
                    "--model",
                    request.model,
                    "--output-last-message",
                    str(output_file),
                    _build_prompt(request.messages),
                ],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(_format_process_error(completed.stderr, completed.stdout))
            return ChatResponse(content=output_file.read_text(encoding="utf-8").strip())
        finally:
            if output_file is not None:
                output_file.unlink(missing_ok=True)

    def stream_chat_completion(self, request: ChatRequest) -> Iterable[ChatChunk]:
        process = subprocess.Popen(
            [
                self._codex_binary,
                "exec",
                "--skip-git-repo-check",
                "--model",
                request.model,
                "--json",
                _build_prompt(request.messages),
            ],
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
                if "delta" in str(event.get("type") or ""):
                    seen_delta = True
                yield ChatChunk(content=text, raw=event)
        process.wait(timeout=self._timeout_seconds)
        if process.returncode != 0:
            raise RuntimeError(_format_process_error("\n".join(log_lines), ""))


class _CodexCompatChatCompletions:
    def __init__(self, client: _CodexCliClient) -> None:
        self._client = client

    def create(self, **kwargs):
        from agent_runtime_framework.models.chat import ChatMessage

        request = ChatRequest(
            model=str(kwargs["model"]),
            messages=[
                ChatMessage(role=str(item.get("role") or ""), content=str(item.get("content") or ""))
                for item in kwargs.get("messages") or []
            ],
        )
        if kwargs.get("stream"):
            return _compat_stream(self._client.stream_chat_completion(request))
        response = self._client.create_chat_completion(request)
        return _compat_response(response.content)


class _CodexCompatChat:
    def __init__(self, client: _CodexCliClient) -> None:
        self.completions = _CodexCompatChatCompletions(client)


def _compat_response(content: str):
    return type(
        "ChatCompletionResponse",
        (),
        {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]},
    )()


def _compat_stream(chunks: Iterable[ChatChunk]):
    for chunk in chunks:
        yield type(
            "ChatCompletionChunk",
            (),
            {"choices": [type("Choice", (), {"delta": type("Delta", (), {"content": chunk.content})()})()]},
        )()


@dataclass(slots=True)
class CodexCliInstance(ModelInstance):
    instance_id: str = "codex_local"
    codex_binary: str = "codex"
    auth_file: Path = field(default_factory=lambda: Path.home() / ".codex" / "auth.json")
    timeout_seconds: int = 120
    available_models: list[ModelProfile] = field(default_factory=list)

    def list_models(self) -> list[ModelProfile]:
        return [ModelProfile(**{**item.as_dict(), "instance": self.instance_id}) for item in self.available_models]

    def authenticate(self, credentials: dict[str, Any], store: CredentialStore) -> AuthSession:
        codex_binary = str(credentials.get("codex_binary") or self.codex_binary).strip() or self.codex_binary
        auth_file = Path(str(credentials.get("auth_file") or self.auth_file)).expanduser()
        if shutil.which(codex_binary) is None:
            return AuthSession(
                instance=self.instance_id,
                authenticated=False,
                auth_type="local_session",
                error_message="codex_cli_not_found",
            )
        auth_payload = _read_auth_payload(auth_file)
        if auth_payload is None:
            return AuthSession(
                instance=self.instance_id,
                authenticated=False,
                auth_type="local_session",
                error_message="codex_auth_missing",
            )
        store.set(self.instance_id, {"codex_binary": codex_binary, "auth_file": str(auth_file)})
        return AuthSession(
            instance=self.instance_id,
            authenticated=True,
            auth_type="local_session",
            metadata={"auth_mode": str(auth_payload.get("auth_mode") or "")},
        )

    def get_client(self, store: CredentialStore) -> Any:
        stored = store.get(self.instance_id) or {}
        codex_binary = str(stored.get("codex_binary") or self.codex_binary).strip() or self.codex_binary
        auth_file = Path(str(stored.get("auth_file") or self.auth_file)).expanduser()
        if shutil.which(codex_binary) is None:
            return None
        if _read_auth_payload(auth_file) is None:
            return None
        return _CodexCliClient(codex_binary=codex_binary, timeout_seconds=self.timeout_seconds)


@dataclass(slots=True)
class CodexCliDriver(ModelDriver):
    driver_type: str = "codex_cli"
    timeout_seconds: int = 120
    capabilities: DriverCapabilities = field(default_factory=lambda: DriverCapabilities(
        supports_stream=True,
        supports_tools=False,
        supports_vision=False,
        supports_json_mode=False,
    ))

    def create_instance(self, instance_id: str, config: dict[str, Any]) -> CodexCliInstance:
        connection = dict(config.get("connection") or {})
        catalog = dict(config.get("catalog") or {})
        model_names = [str(item).strip() for item in list(catalog.get("models") or []) if str(item).strip()]
        return CodexCliInstance(
            instance_id=instance_id,
            codex_binary=str(connection.get("codex_binary") or "codex"),
            auth_file=Path(str(connection.get("auth_file") or "~/.codex/auth.json")).expanduser(),
            timeout_seconds=self.timeout_seconds,
            available_models=[_profile_for_model(instance_id, model_name) for model_name in model_names],
        )


def _build_prompt(messages: list[Any]) -> str:
    if not messages:
        return "请直接给出简洁回复。"
    rendered: list[str] = []
    for message in messages:
        role = str(getattr(message, "role", "user") or "user").strip().upper()
        content = str(getattr(message, "content", "") or "").strip()
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


def _format_process_error(stderr: str, stdout: str) -> str:
    detail = (stderr or stdout or "").strip()
    return detail[:400] if detail else "codex_local request failed"


def _profile_for_model(instance_id: str, model_name: str) -> ModelProfile:
    known = {
        "gpt-5.3-codex": ("GPT-5.3 Codex", "medium", "medium", "high"),
        "gpt-5.4": ("GPT-5.4", "high", "medium", "high"),
        "gpt-5.4-mini": ("GPT-5.4 Mini", "low", "low", "medium"),
    }
    display_name, cost_level, latency_level, reasoning_level = known.get(
        model_name,
        (model_name, "medium", "medium", "medium"),
    )
    return ModelProfile(
        instance=instance_id,
        model_name=model_name,
        display_name=display_name,
        cost_level=cost_level,
        latency_level=latency_level,
        reasoning_level=reasoning_level,
        recommended_roles=["conversation", "capability_selector", "planner"],
    )
