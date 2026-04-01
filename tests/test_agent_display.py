from agent_runtime_framework.display import build_display_profile, color_for_agent, format_run_label


def test_agent_display_profile_has_stable_color():
    first = color_for_agent("workspace")
    second = color_for_agent("workspace")
    profile = build_display_profile("workspace", "Workspace Agent")

    assert first == second
    assert profile.color == first
    assert "Workspace Agent" in format_run_label(profile, "running")
