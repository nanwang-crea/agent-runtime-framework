from __future__ import annotations

import json
from pathlib import Path

from agent_runtime_framework.agents.definitions import AgentDefinition
from agent_runtime_framework.agents.registry import AgentRegistry


def load_agent_definitions_from_dir(path: str | Path) -> list[AgentDefinition]:
    root = Path(path).expanduser()
    definitions: list[AgentDefinition] = []
    if not root.exists():
        return definitions
    for entry in sorted(root.glob('*.json')):
        parsed = json.loads(entry.read_text(encoding='utf-8'))
        definitions.append(
            AgentDefinition(
                agent_id=str(parsed.get('agent_id') or '').strip(),
                label=str(parsed.get('label') or '').strip(),
                description=str(parsed.get('description') or '').strip(),
                kind=str(parsed.get('kind') or 'agent').strip(),
                default_persona=str(parsed.get('default_persona') or 'general').strip(),
                executor_kind=str(parsed.get('executor_kind') or 'workflow').strip(),
            )
        )
    return [definition for definition in definitions if definition.agent_id and definition.label]


def extend_registry_from_dir(registry: AgentRegistry, path: str | Path) -> AgentRegistry:
    for definition in load_agent_definitions_from_dir(path):
        registry.register(definition)
    return registry
