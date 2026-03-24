# Agent Assessment And Sandbox Plan

## Current Assessment

The current system is directionally correct for a modern action-centric agent:

- `router` separates conversation from task execution
- `planner` focuses on next action selection
- `tools` are moving toward atomic operations
- `output_evaluator` adds continue-or-finish behavior
- `approval / resume` already exists for risky actions

This is meaningfully closer to Codex-style agent behavior than the earlier capability-centric loop.

## Main Gap Versus Mature Agents

The most important missing layer is **true execution sandboxing**.

Today the framework has:

- logical permission levels
- approval checkpoints
- workspace-root path checks for file tools

But it does **not** yet have:

- process-level sandboxing for shell execution
- network isolation policy
- per-command filesystem isolation
- temporary execution environments
- explicit sandbox mode configuration such as `read-only / workspace-write / full-access`

That means the current system has a **policy layer**, but not a **real sandbox layer**.

## Why This Matters

Mainstream coding agents distinguish between:

1. `routing / planning intelligence`
2. `tool execution policy`
3. `sandbox + approvals`

Without the third layer, an agent may be explainable and approval-aware, but it is still not execution-safe enough for broader autonomous use.

## Current State In This Repo

### Already Present

- `SimpleDesktopPolicy`
- permission levels on tools
- approval for `high` and `destructive` actions
- allowed workspace root enforcement for workspace path tools

### Still Missing

- sandbox mode abstraction
- shell command sandbox adapter
- network access mode
- sandbox-aware artifact/logging of blocked operations
- UI-visible sandbox state

## Recommended Upgrade Order

### Priority 1: Add Sandbox Modes

Introduce explicit runtime modes:

- `read_only`
- `workspace_write`
- `danger_full_access`

These should be first-class runtime settings, not implicit behavior.

### Priority 2: Add Sandbox-Aware Command Execution

Wrap `run_shell_command` behind a command runner that can:

- deny writes in `read_only`
- restrict cwd and writable roots in `workspace_write`
- optionally block network
- emit structured failure reasons when sandbox denies execution

Current implementation slice now started:

- added `agent_runtime_framework/sandbox/`
- introduced `SandboxConfig`
- switched `run_shell_command` away from `shell=True`
- commands are now parsed with `shlex.split()`
- shell metacharacters such as `&&`, `|`, `;`, redirection, and command substitution are denied
- common network commands such as `curl`, `wget`, and `ssh` are blocked by default
- demo context payload now exposes current sandbox state

This is intentionally only the first slice. It is a command sandbox adapter, not yet a full process jail.

### Priority 3: Surface Sandbox State In Trace And UI

The user should be able to see:

- current sandbox mode
- whether a command used sandbox or full access
- whether a failure came from sandbox denial, approval denial, or tool failure

### Priority 4: Make Evaluator And Planner Sandbox-Aware

Planner and evaluator should know:

- command execution may be blocked by sandbox
- some tasks need approval escalation
- some tasks should propose safer alternatives when sandbox blocks execution

## Near-Term Plan

1. keep the current router / planner / evaluator architecture
2. do not revert to capability-centric control flow
3. add a real `sandbox` module to the kernel layer
4. route shell execution through that module before expanding agent autonomy further

## Implemented First Slice

The first minimal sandbox slice now covers:

1. explicit `SandboxConfig` runtime object
2. default mode `workspace_write`
3. command allowlist and network-command denylist
4. no `shell=True` for default shell tool execution
5. UI-visible sandbox payload through demo context

Still not covered yet:

- filesystem sandbox for write tools
- per-tool sandbox policy unification
- planner / evaluator awareness of sandbox denial
- approval escalation based on sandbox mode
- process/container isolation

## Key Principle

The current agent is **architecturally promising but not yet product-grade safe**.

The next major maturity jump is not “smarter planning”; it is:

`smart planning + explicit sandbox + approval-aware execution isolation`
