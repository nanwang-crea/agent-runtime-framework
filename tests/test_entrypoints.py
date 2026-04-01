from pathlib import Path

from agent_runtime_framework.demo import create_demo_assistant_app
from agent_runtime_framework.entrypoints import AgentRequest, run_agent_request, run_cli_entry
from tests.test_demo_app import _install_conversation_model


def test_sdk_and_cli_entrypoints_share_same_app_flow(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    app = create_demo_assistant_app(workspace)
    _install_conversation_model(app)
    sdk = run_agent_request(app, AgentRequest(message="你是谁？", agent_id="qa_only"))
    cli = run_cli_entry(app, message="你是谁？", agent_id="qa_only")

    assert sdk.status == "completed"
    assert cli.status == "completed"
    assert sdk.agent_id == "qa_only"
    assert cli.agent_id == "qa_only"
