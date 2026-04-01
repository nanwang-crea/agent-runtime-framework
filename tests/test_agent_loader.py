import json
from pathlib import Path

from agent_runtime_framework.agents import AgentRegistry
from agent_runtime_framework.agents.loader import extend_registry_from_dir, load_agent_definitions_from_dir


def test_agent_loader_reads_json_definitions(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "review.json").write_text(json.dumps({"agent_id": "review", "label": "Review Agent"}), encoding="utf-8")

    definitions = load_agent_definitions_from_dir(agents_dir)

    assert len(definitions) == 1
    assert definitions[0].agent_id == "review"

    registry = extend_registry_from_dir(AgentRegistry(), agents_dir)
    assert registry.require("review").label == "Review Agent"
