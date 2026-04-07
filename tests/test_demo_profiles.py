from agent_runtime_framework.demo.profiles import builtin_demo_profiles, get_demo_profile


def test_builtin_demo_profiles_cover_current_context_switcher_options():
    profiles = builtin_demo_profiles()

    assert [profile.profile_id for profile in profiles] == [
        "workspace",
        "qa_only",
        "explore",
        "plan",
        "verification",
        "conversation",
    ]
    assert get_demo_profile("explore").label == "Explore Agent"


def test_demo_profile_payload_is_frontend_friendly():
    payload = get_demo_profile("workspace").to_payload()

    assert payload == {
        "id": "workspace",
        "label": "Workspace Agent",
        "kind": "agent",
    }
