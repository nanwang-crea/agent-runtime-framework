# Agent Architecture Migration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Evolve the current workflow-first runtime into a five-layer agent architecture with explicit entrypoints, agent-tool orchestration, built-in agent definitions, extensible runtime execution, and stronger supporting infrastructure.

**Architecture:** Keep `WorkflowRuntime` as the top-level execution kernel, but insert two missing middle layers: a first-class `AgentDefinition` registry and a unified `AgentTool` orchestration surface. Promote `CodexAgentLoop` from a hidden backend into one executable engine behind graph nodes and agent tools, then add subagent sessioning, agent loading, and richer display/coordination support incrementally.

**Tech Stack:** Python dataclasses, workflow runtime, tool registry, Markdown memory, demo server/app, pytest.

### Task 1: Lock the target five-layer architecture

**Files:**
- Create: `docs/architecture/agent-stack-target.md`
- Modify: `README.md`
- Modify: `docs/通用Agent.md`

**Step 1: Write the target layer contract**
- Define the five layers explicitly:
  - Entry Trigger Layer
  - AgentTool Orchestration Layer
  - Agent Definition Layer
  - Runtime Execution Layer
  - Supporting Capability Layer
- For each layer, specify ownership, public API, and what it must not do.

**Step 2: Record current-to-target mapping**
- Map current modules to target layers.
- Mark each area as: `ready`, `partial`, or `missing`.

**Step 3: Document the key architectural rule**
- Rule: `DemoAssistantApp` should orchestrate routing and presentation, but not encode agent definitions or runtime internals.
- Rule: `WorkflowRuntime` remains the execution kernel.
- Rule: `CodexAgentLoop` becomes one executor backend, not the whole product surface.

**Step 4: Add migration notes to README**
- Update top-level architecture bullets to reflect the new target stack.

**Step 5: Commit**
```bash
git add docs/architecture/agent-stack-target.md README.md docs/通用Agent.md
git commit -m "docs: define target five-layer agent architecture"
```

### Task 2: Create the Agent Definition Layer

**Files:**
- Create: `agent_runtime_framework/agents/definitions.py`
- Create: `agent_runtime_framework/agents/registry.py`
- Create: `agent_runtime_framework/agents/builtin.py`
- Modify: `agent_runtime_framework/agents/__init__.py`
- Test: `tests/test_agent_registry.py`

**Step 1: Write the failing test**
- Add tests proving the system can register and retrieve built-in agents like:
  - `general_purpose`
  - `explore`
  - `plan`
  - `verification`
  - `conversation`

**Step 2: Run test to verify it fails**
Run:
```bash
pytest tests/test_agent_registry.py -q
```
Expected: FAIL because no registry or built-in definitions exist.

**Step 3: Write minimal implementation**
- Add `AgentDefinition` with fields like:
  - `agent_id`
  - `label`
  - `description`
  - `default_persona`
  - `allowed_tool_names`
  - `workflow_preferences`
  - `supports_subagents`
  - `executor_kind`
- Add `AgentRegistry` with:
  - `register()`
  - `get()`
  - `list()`
  - `require()`
- Add `builtin.py` to expose built-in definitions.

**Step 4: Run test to verify it passes**
Run:
```bash
pytest tests/test_agent_registry.py -q
```
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/agents/definitions.py agent_runtime_framework/agents/registry.py agent_runtime_framework/agents/builtin.py agent_runtime_framework/agents/__init__.py tests/test_agent_registry.py
git commit -m "feat: add built-in agent definition registry"
```

### Task 3: Build the AgentTool Orchestration Layer

**Files:**
- Create: `agent_runtime_framework/agent_tools/models.py`
- Create: `agent_runtime_framework/agent_tools/registry.py`
- Create: `agent_runtime_framework/agent_tools/executor.py`
- Create: `agent_runtime_framework/agent_tools/prompts.py`
- Modify: `agent_runtime_framework/tools/registry.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_agent_tools.py`

**Step 1: Write the failing test**
- Add tests proving an `AgentTool` can:
  - validate input
  - resolve agent definition
  - choose workflow/runtime backend
  - return a normalized payload

**Step 2: Run test to verify it fails**
Run:
```bash
pytest tests/test_agent_tools.py -q
```
Expected: FAIL because no `AgentTool` abstraction exists.

**Step 3: Write minimal implementation**
- Add `AgentToolSpec` and `AgentToolCall` models.
- Add executor flow:
  - resolve `AgentDefinition`
  - construct execution request
  - invoke `WorkflowRuntime` or direct conversation backend
  - normalize UI payload
- Keep `ToolRegistry` for raw tools; do not overload it with agent-specific semantics.

**Step 4: Integrate one vertical slice**
- Convert one built-in agent, e.g. `explore`, into an `AgentTool`-backed path.
- Make demo app able to invoke it without hardcoding the agent logic inside `DemoAssistantApp`.

**Step 5: Run tests**
Run:
```bash
pytest tests/test_agent_tools.py tests/test_demo_app.py -q
```
Expected: PASS

**Step 6: Commit**
```bash
git add agent_runtime_framework/agent_tools/models.py agent_runtime_framework/agent_tools/registry.py agent_runtime_framework/agent_tools/executor.py agent_runtime_framework/agent_tools/prompts.py agent_runtime_framework/tools/registry.py agent_runtime_framework/demo/app.py tests/test_agent_tools.py
git commit -m "feat: add agent tool orchestration layer"
```

### Task 4: Separate Entry Trigger Layer cleanly

**Files:**
- Create: `agent_runtime_framework/entrypoints/sdk.py`
- Create: `agent_runtime_framework/entrypoints/cli.py`
- Create: `agent_runtime_framework/entrypoints/models.py`
- Modify: `agent_runtime_framework/demo/server.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_entrypoints.py`

**Step 1: Write the failing test**
- Add tests proving the same agent request can be triggered from:
  - server route
  - SDK call
  - CLI-like entry adapter

**Step 2: Run test to verify it fails**
Run:
```bash
pytest tests/test_entrypoints.py -q
```
Expected: FAIL because entry handling is fused into demo modules.

**Step 3: Write minimal implementation**
- Add a normalized `AgentRequest` / `AgentResponse` model.
- Move request parsing and external trigger normalization into `entrypoints/`.
- Keep `DemoAssistantApp` as a composition root, not the only request adapter.

**Step 4: Run tests**
Run:
```bash
pytest tests/test_entrypoints.py tests/test_demo_app.py -q
```
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/entrypoints/sdk.py agent_runtime_framework/entrypoints/cli.py agent_runtime_framework/entrypoints/models.py agent_runtime_framework/demo/server.py agent_runtime_framework/demo/app.py tests/test_entrypoints.py
git commit -m "refactor: extract agent entry trigger layer"
```

### Task 5: Upgrade Runtime Execution Layer for real subagents

**Files:**
- Create: `agent_runtime_framework/runtime/agent_runtime.py`
- Create: `agent_runtime_framework/runtime/subagents.py`
- Create: `agent_runtime_framework/runtime/agent_sessions.py`
- Modify: `agent_runtime_framework/workflow/runtime.py`
- Modify: `agent_runtime_framework/workflow/codex_subtask.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `agent_runtime_framework/agents/codex/personas.py`
- Test: `tests/test_subagent_runtime.py`

**Step 1: Write the failing test**
- Add tests for:
  - `run_agent()`
  - `resume_agent()`
  - `fork_subagent()`
  - parent-child run metadata
  - inherited memory snapshot

**Step 2: Run test to verify it fails**
Run:
```bash
pytest tests/test_subagent_runtime.py -q
```
Expected: FAIL because subagent runtime does not exist yet.

**Step 3: Write minimal implementation**
- Add `AgentSessionRecord` and `SubagentLink` models.
- Add runtime APIs:
  - `run_agent(request)`
  - `resume_agent(token)`
  - `fork_subagent(parent_run, agent_id, goal)`
- Keep the actual execution kernel delegated to `WorkflowRuntime`.
- Make `codex_subtask` able to run in a child agent session instead of only the same session.

**Step 4: Run tests**
Run:
```bash
pytest tests/test_subagent_runtime.py tests/test_workflow_runtime.py -q
```
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/runtime/agent_runtime.py agent_runtime_framework/runtime/subagents.py agent_runtime_framework/runtime/agent_sessions.py agent_runtime_framework/workflow/runtime.py agent_runtime_framework/workflow/codex_subtask.py agent_runtime_framework/agents/codex/loop.py agent_runtime_framework/agents/codex/personas.py tests/test_subagent_runtime.py
git commit -m "feat: add sessionized subagent runtime"
```

### Task 6: Add external agent loading and extension points

**Files:**
- Create: `agent_runtime_framework/agents/loader.py`
- Create: `agent_runtime_framework/agents/schema.py`
- Modify: `agent_runtime_framework/agents/registry.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_agent_loader.py`

**Step 1: Write the failing test**
- Add tests for loading agent definitions from a directory.
- Validate bad config rejection and override rules.

**Step 2: Run test to verify it fails**
Run:
```bash
pytest tests/test_agent_loader.py -q
```
Expected: FAIL because dynamic loading is missing.

**Step 3: Write minimal implementation**
- Support loading agent metadata from local JSON/YAML files.
- Restrict initial scope to definitions only, not arbitrary Python plugins.
- Merge loaded agents into the registry with deterministic override rules.

**Step 4: Run tests**
Run:
```bash
pytest tests/test_agent_loader.py -q
```
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/agents/loader.py agent_runtime_framework/agents/schema.py agent_runtime_framework/agents/registry.py agent_runtime_framework/demo/app.py tests/test_agent_loader.py
git commit -m "feat: add load-agents-dir style extension point"
```

### Task 7: Strengthen the Supporting Capability Layer

**Files:**
- Create: `agent_runtime_framework/display/models.py`
- Create: `agent_runtime_framework/display/formatting.py`
- Create: `agent_runtime_framework/display/color_manager.py`
- Create: `agent_runtime_framework/swarm/models.py`
- Create: `agent_runtime_framework/swarm/coordinator.py`
- Modify: `agent_runtime_framework/memory/index.py`
- Modify: `agent_runtime_framework/observability/events.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_agent_display.py`
- Test: `tests/test_swarm_coordinator.py`

**Step 1: Write the failing tests**
- Add tests for:
  - multi-agent display identity
  - stable color assignment
  - run-tree rendering metadata
  - lightweight swarm coordination state

**Step 2: Run test to verify they fail**
Run:
```bash
pytest tests/test_agent_display.py tests/test_swarm_coordinator.py -q
```
Expected: FAIL because display and swarm abstractions are missing.

**Step 3: Write minimal implementation**
- Add display metadata models for agent labels, colors, run lineage, and compact status.
- Add a minimal swarm coordinator that tracks sibling/child runs and handoff intents.
- Extend memory search to support agent-scoped filters and run lineage metadata.

**Step 4: Run tests**
Run:
```bash
pytest tests/test_agent_display.py tests/test_swarm_coordinator.py tests/test_memory_and_policy.py -q
```
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/display/models.py agent_runtime_framework/display/formatting.py agent_runtime_framework/display/color_manager.py agent_runtime_framework/swarm/models.py agent_runtime_framework/swarm/coordinator.py agent_runtime_framework/memory/index.py agent_runtime_framework/observability/events.py agent_runtime_framework/demo/app.py tests/test_agent_display.py tests/test_swarm_coordinator.py
git commit -m "feat: strengthen display and swarm support layers"
```

### Task 8: Move current demo agent selection onto the new stack

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/assistant/conversation.py`
- Modify: `agent_runtime_framework/agents/builtin.py`
- Modify: `agent_runtime_framework/agent_tools/executor.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing test**
- Add tests that demo app agent switching uses `AgentRegistry` definitions instead of hardcoded IDs.

**Step 2: Run test to verify it fails**
Run:
```bash
pytest tests/test_demo_app.py -q
```
Expected: FAIL because `codex` and `qa_only` are still hardcoded.

**Step 3: Write minimal implementation**
- Replace hardcoded `available_agents` payload with registry-backed data.
- Keep `qa_only` as a built-in conversation agent and `codex` as a general-purpose workspace agent.

**Step 4: Run tests**
Run:
```bash
pytest tests/test_demo_app.py -q
```
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/demo/app.py agent_runtime_framework/assistant/conversation.py agent_runtime_framework/agents/builtin.py agent_runtime_framework/agent_tools/executor.py tests/test_demo_app.py
git commit -m "refactor: move demo agent selection to registry-backed stack"
```

### Task 9: Final hardening and public surface cleanup

**Files:**
- Modify: `agent_runtime_framework/__init__.py`
- Modify: `agent_runtime_framework/agents/__init__.py`
- Modify: `agent_runtime_framework/runtime/__init__.py`
- Modify: `README.md`
- Modify: `docs/通用Agent.md`
- Test: `tests/test_public_surface.py`

**Step 1: Write the failing test**
- Add public API tests for the new runtime, registry, entrypoints, and agent-tool layer.

**Step 2: Run test to verify it fails**
Run:
```bash
pytest tests/test_public_surface.py -q
```
Expected: FAIL because public exports are incomplete.

**Step 3: Write minimal implementation**
- Export the new public objects cleanly.
- Remove stale references that imply `CodexAgentLoop` is still the top runtime.

**Step 4: Run final focused suite**
Run:
```bash
pytest tests/test_agent_registry.py tests/test_agent_tools.py tests/test_entrypoints.py tests/test_subagent_runtime.py tests/test_agent_loader.py tests/test_agent_display.py tests/test_swarm_coordinator.py tests/test_demo_app.py tests/test_workflow_runtime.py -q
```
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/__init__.py agent_runtime_framework/agents/__init__.py agent_runtime_framework/runtime/__init__.py README.md docs/通用Agent.md tests/test_public_surface.py
git commit -m "chore: finalize five-layer agent architecture public surface"
```

## Recommended rollout order

1. `Agent Definition Layer`
2. `AgentTool Orchestration Layer`
3. `Entry Trigger Layer`
4. `Runtime Execution Layer` subagents
5. `External agent loading`
6. `Supporting display/swarm upgrades`
7. `Demo migration`
8. `Public surface cleanup`

## Scope guardrails

- Do not replace `WorkflowRuntime`; build around it.
- Do not make dynamic Python plugin loading the first extension mechanism; start with declarative agent definitions.
- Do not implement full swarm autonomy in phase one; start with lineage, coordination state, and handoff metadata.
- Do not collapse agent definitions into personas; personas remain a lower-level execution hint.
- Do not move all existing tools into `AgentTool`; raw tools and agent-tools should stay as separate abstractions.

## Phase-by-phase outcome targets

- **Phase 1:** We can answer “有哪些 agent” with a real registry.
- **Phase 2:** We can invoke an agent as a first-class callable capability.
- **Phase 3:** We can trigger the same agent flow from server, SDK, and CLI adapters.
- **Phase 4:** We can run, resume, and fork subagents with lineage.
- **Phase 5:** We can load external agent definitions safely.
- **Phase 6:** We can visualize and trace multiple agents coherently.
- **Phase 7:** Demo app becomes a consumer of the new stack, not the place where the stack is defined.
