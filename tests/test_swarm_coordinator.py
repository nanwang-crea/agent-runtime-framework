from agent_runtime_framework.swarm import SwarmCoordinator


def test_swarm_coordinator_tracks_child_handoffs():
    coordinator = SwarmCoordinator()
    state = coordinator.open("root-1")
    updated = coordinator.add_child("root-1", "child-1", agent_id="explore")

    assert state.root_session_id == "root-1"
    assert "child-1" in updated.active_session_ids
    assert updated.handoffs[0]["agent_id"] == "explore"
