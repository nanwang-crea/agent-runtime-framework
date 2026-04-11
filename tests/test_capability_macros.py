from agent_runtime_framework.capabilities.defaults import default_capability_macros


def test_default_macros_define_chains():
    macros = default_capability_macros()
    ids = {m.macro_id for m in macros}
    assert "resolve_target_then_read" in ids
    chain = next(m for m in macros if m.macro_id == "inspect_and_patch_file").capability_chain
    assert "read_workspace_evidence" in chain
    assert "run_workspace_verification" in chain
