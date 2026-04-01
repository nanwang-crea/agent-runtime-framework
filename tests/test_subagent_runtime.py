from pathlib import Path

from agent_runtime_framework.demo import create_demo_assistant_app
from agent_runtime_framework.entrypoints import AgentRequest
from agent_runtime_framework.runtime import AgentRuntime
from tests.test_demo_app import _install_conversation_model


def test_agent_runtime_can_run_and_fork_subagent(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    app = create_demo_assistant_app(workspace)
    _install_conversation_model(app)
    runtime = AgentRuntime(app)

    parent = runtime.run_agent(AgentRequest(message="你是谁？", agent_id="qa_only"))
    parent_session_id = str(parent.metadata.get("session_id") or "")
    child = runtime.fork_subagent(parent_session_id, agent_id="qa_only", goal="再说一遍你是谁？")

    assert parent.status == "completed"
    assert child.status == "completed"
    assert runtime.links
    assert runtime.links[0].parent_session_id == parent_session_id
