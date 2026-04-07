from agent_runtime_framework.api.models.profiles import builtin_profiles, get_profile


def test_builtin_profiles_cover_current_context_switcher_options():
    profiles = builtin_profiles()

    assert [profile.profile_id for profile in profiles] == [
        "workspace",
        "qa_only",
        "explore",
        "plan",
        "verification",
        "conversation",
    ]
    assert get_profile("explore").label == "Explore Agent"


def test_profile_payload_is_frontend_friendly():
    payload = get_profile("workspace").to_payload()

    assert payload == {
        "id": "workspace",
        "label": "Workspace Agent",
        "kind": "agent",
    }
