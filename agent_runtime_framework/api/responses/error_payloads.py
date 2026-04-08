from __future__ import annotations

from typing import Any

from agent_runtime_framework.api.responses.common_payloads import with_router_trace
from agent_runtime_framework.errors import AppError, normalize_app_error


def normalize_api_error(
    exc: Exception,
    *,
    workspace: str,
    route_decision: dict[str, str] | None,
) -> AppError:
    base_context = {
        "workspace": workspace,
        "route": str((route_decision or {}).get("route") or ""),
        "route_source": str((route_decision or {}).get("source") or ""),
    }
    if isinstance(exc, AppError):
        return normalize_app_error(exc, context=base_context)
    if isinstance(exc, FileNotFoundError):
        return AppError(
            code="RESOURCE_NOT_FOUND",
            message="未找到目标资源。",
            detail=str(exc),
            stage="resolve",
            retriable=True,
            suggestion="请检查路径或文件名是否正确。",
            context=base_context,
        )
    if isinstance(exc, IsADirectoryError):
        return AppError(
            code="RESOURCE_IS_DIRECTORY",
            message="目标是目录，当前操作只接受文件。",
            detail=str(exc),
            stage="execute",
            retriable=True,
            suggestion="可以先列出目录内容，或指定目录下的某个文件。",
            context=base_context,
        )
    if isinstance(exc, NotADirectoryError):
        return AppError(
            code="RESOURCE_NOT_DIRECTORY",
            message="目标不是目录，无法执行目录操作。",
            detail=str(exc),
            stage="execute",
            retriable=True,
            suggestion="请改为读取文件，或重新指定目录。",
            context=base_context,
        )
    if isinstance(exc, ValueError) and "outside allowed roots" in str(exc):
        return AppError(
            code="RESOURCE_OUTSIDE_WORKSPACE",
            message="目标超出了当前工作区范围。",
            detail=str(exc),
            stage="resolve",
            retriable=False,
            suggestion="请只操作当前工作区内的文件或目录。",
            context=base_context,
        )
    detail = f"{type(exc).__name__}: {exc}"
    if "llm_unavailable" in detail:
        return normalize_app_error(
            exc,
            code="MODEL_UNAVAILABLE",
            message=str(exc),
            stage="conversation_response",
            retriable=False,
            suggestion="请先在前端“模型 / 配置”中为 conversation 配置可用模型。",
            context={**base_context, "exception_type": type(exc).__name__},
        )
    return normalize_app_error(
        exc,
        code="INTERNAL_ERROR",
        message="处理请求时发生了未预期错误。",
        stage="run",
        retriable=False,
        suggestion="可以重试一次；如果持续出现，请检查后端日志。",
        context={**base_context, "exception_type": type(exc).__name__},
    )


def error_payload(
    *,
    exc: Exception,
    workspace: str,
    route_decision: dict[str, str] | None,
    session_payload: dict[str, Any],
    plan_history: list[dict[str, Any]],
    memory_payload: dict[str, Any],
    context_payload: dict[str, Any],
) -> tuple[AppError, dict[str, Any]]:
    error = normalize_api_error(
        exc,
        workspace=workspace,
        route_decision=route_decision,
    )
    payload = {
        "status": "error",
        "final_answer": error.message,
        "execution_trace": with_router_trace(
            route_decision,
            [{"name": error.stage or "run", "status": "error", "detail": f"{error.code}: {error.message}"}],
        ),
        "approval_request": None,
        "resume_token_id": None,
        "session": session_payload,
        "plan_history": plan_history,
        "memory": memory_payload,
        "context": context_payload,
        "error": error.as_dict(),
        "workspace": workspace,
    }
    return error, payload
