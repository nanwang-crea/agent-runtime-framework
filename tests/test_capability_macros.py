from agent_runtime_framework.capabilities.defaults import default_capability_macros


def test_default_macros_define_chains():
    macros = default_capability_macros()
    ids = {m.macro_id for m in macros}
    assert "resolve_then_read_target" in ids
    assert "resolve_then_create_path" in ids
    assert "locate_inspect_edit_verify" in ids
    chain = next(m for m in macros if m.macro_id == "locate_inspect_edit_verify").capability_chain
    assert "read_workspace_evidence" in chain
    assert "run_workspace_verification" in chain
