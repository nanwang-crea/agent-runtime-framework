# Conversation / Codex Decoupling Checklist

## Goal

Make ordinary conversation and Codex task execution independent paths:

- normal chat should go directly to `conversation`
- task-oriented workspace requests should go to `codex planner -> action loop`
- planner failures must not block plain conversation

## Checklist

- [x] Add a dedicated conversation gate before Codex planning.
- [x] Make both `chat()` and `stream_chat()` use the same gate.
- [x] Ensure plain greetings and Q&A do not invoke the planner at all.
- [x] Keep workspace actions on the Codex path so planner/tool requirements still apply.
- [x] Add regression tests for greeting routing in both sync and streaming flows.
- [ ] Narrow the deterministic fallback gate with a more explicit task-intent schema instead of broad keyword matching.
- [x] Add a dedicated `router` model role so conversation-vs-task classification can be model-assisted without reusing the strict planner schema.
- [ ] Surface routing decisions in the UI trace as `conversation_gate -> conversation` or `conversation_gate -> codex`.
- [ ] Add planner output examples and stronger schema hints so task planning is less brittle.
- [ ] Move the same routing rule into the broader runtime layer so demo-specific behavior does not drift from the main assistant/runtime behavior.

## Current Decision

The project now uses a two-stage router:

- if a `router` role model is configured, it decides `conversation` vs `codex`
- if router is unavailable or returns invalid output, the deterministic gate takes over

This keeps plain conversation out of the strict planner schema while preserving a safe fallback path.
