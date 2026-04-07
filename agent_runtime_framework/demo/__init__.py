"""Local demo app and server for the desktop assistant runtime."""

from agent_runtime_framework.demo.app import DemoAssistantApp
from agent_runtime_framework.demo.bootstrap import create_demo_assistant_app

__all__ = [
    "DemoAssistantApp",
    "create_demo_assistant_app",
]
