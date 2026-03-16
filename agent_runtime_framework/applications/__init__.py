"""Application orchestration layer and desktop content reference app."""

from agent_runtime_framework.applications.core import (
    ApplicationContext,
    ApplicationRunner,
    ApplicationSpec,
)
from agent_runtime_framework.applications.desktop_actions import DesktopActionHandlerRegistry
from agent_runtime_framework.applications.desktop import create_desktop_content_application
from agent_runtime_framework.applications.structured import run_stage_parser

__all__ = [
    "ApplicationContext",
    "DesktopActionHandlerRegistry",
    "ApplicationRunner",
    "ApplicationSpec",
    "create_desktop_content_application",
    "run_stage_parser",
]
