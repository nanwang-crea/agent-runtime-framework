from agent_runtime_framework.capabilities.defaults import build_default_capability_registry
from agent_runtime_framework.capabilities.models import CapabilitySpec
from agent_runtime_framework.capabilities.registry import CapabilityRegistry


def test_default_registry_contains_core_capabilities():
    reg = build_default_capability_registry()
    ids = {spec["capability_id"] for spec in reg.list_payloads()}
    assert "read_workspace_evidence" in ids
    assert "run_workspace_verification" in ids
    assert len(ids) >= 5


def test_registry_match_failure_maps_signatures():
    reg = build_default_capability_registry()
    matched = reg.match_failure({"category": "planning_gap", "summary": "read_plan missing for target"})
    assert "read_workspace_evidence" in matched


def test_register_rejects_duplicates():
    reg = CapabilityRegistry()
    spec = CapabilitySpec(capability_id="x", description="d", intents=["i"], toolchains=[["tool_call"]])
    reg.register(spec)
    try:
        reg.register(spec)
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")
