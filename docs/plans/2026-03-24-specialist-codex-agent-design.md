# Specialist Codex Agent Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current Codex loop into a single extensible agent with task profiles for chat, repository explanation, and change-and-verify workflows.

**Architecture:** Keep one `CodexAgentLoop` as the main runtime. Add a `task_profile` classification layer on `CodexTask`, then let planner/evaluator/synthesis behave differently based on that profile. This preserves one shared session/memory/tooling stack while allowing specialist behavior to grow profile-by-profile.

**Current Implementation Direction:** Add a lightweight task-level planning layer without replacing the existing action loop. `CodexTask` can now carry `CodexPlan` and `CodexPlanTask`, and profiles can opt into deterministic plan execution while still reusing the same approval, memory, tool, and verification pipeline.

**Tech Stack:** Python 3.12, pytest, dataclasses, existing codex loop/planner/evaluator/tool runtime.

### Design Notes

- `chat`
  - default fallback
  - prefers direct respond
  - uses tools only when user explicitly asks for workspace interaction

- `repository_explainer`
  - first migration target for task-level planning
  - initial plan template is `list_workspace_directory -> inspect_workspace_path -> respond`
  - later can extend to `read_workspace_text` for entry files
  - extracts `structure` and `role` claims
  - final answer should explain architecture, key modules, and file responsibilities

- `change_and_verify`
  - prefers edit/write tools
  - tracks changed paths and pending verifications
  - only finishes after verification clears

- Extensibility
  - new profiles should be added by extending `profiles.py`
  - task-level plan templates should live outside the loop so new profiles can opt in incrementally
  - planner prompt, evaluator gates, and synthesis rules read `task_profile`
  - tools remain shared across profiles
