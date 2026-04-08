from agent_runtime_framework.workflow.context.app_context import ApplicationContext
from agent_runtime_framework.workflow.workspace import WorkspaceContext, build_default_workspace_tools


def test_graph_first_context_modules_expose_application_and_workspace_context():
    assert ApplicationContext is not None
    assert WorkspaceContext is not None
    assert callable(build_default_workspace_tools)
