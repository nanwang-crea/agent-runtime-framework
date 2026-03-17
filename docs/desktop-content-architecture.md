# Agent Runtime Framework Desktop Assistant Architecture

## Summary

`agent-runtime-framework` now includes a first-stage desktop assistant architecture on top of the existing graph and runtime core. The framework keeps graph execution generic, while adding four new framework-level capabilities:

- application orchestration
- resource modeling
- layered memory
- permission and policy decisions

The first built-in application is `desktop_content_application`, which supports directory listing, file resolution, content reading, summary generation, and follow-up references based on session memory. Its intent interpretation is LLM-first when an `llm_client` is available in application context, with a lightweight rule fallback for offline or test-only usage. Structured stage resolution is shared through `run_stage_parser`, so interpret, resolve, plan, execute, and compose can all follow the same precedence: custom service override, then LLM structured parsing, then deterministic fallback. Concrete desktop actions are executed through `DesktopActionHandlerRegistry`, and repository grounding uses `ResolverPipeline`, so desktop-specific behavior is modular without pushing deterministic file operations into the LLM.

The next-stage target is a Codex-style desktop AI assistant built on top of this foundation. Phase 1 of that target is a single-agent loop with skills and MCP extension slots. The framework now includes the first skeleton of that layer: assistant sessions, a unified capability registry, skill registration, static MCP provider slots, and an agent loop that can dispatch to application, skill, or MCP capabilities. This means the framework no longer stops at a single desktop content application and can now start evolving as a true assistant platform.

## Layering

The framework is organized into four layers:

1. Execution kernel: `graph`, `runtime`
2. Foundation capabilities: `tools`, `memory`, `policy`, `observability`
3. Resource semantics: `resources`
4. Application orchestration: `applications`

The execution kernel stays domain-agnostic. Desktop-specific meaning lives in `resources` and `applications`.

## Workflow

Desktop applications run through a fixed workflow:

`interpret -> resolve -> plan -> authorize -> execute -> compose -> remember`

This workflow is implemented by `ApplicationRunner` and specialized by `ApplicationSpec`.

For the desktop AI assistant layer, the target workflow becomes:

`receive -> interpret -> select_capability -> plan -> authorize -> execute -> review -> compose -> remember -> continue_or_stop`

The desktop content workflow remains valid as one capability implementation inside this larger assistant loop.

## Assistant Runtime

The assistant runtime is modeled after Codex-style agent behavior, but scoped to a single primary agent first. This runtime sits above `applications` and is responsible for multi-turn decision making rather than only one-shot application execution.

The current first version introduces these concepts:

- `AssistantSession`: owns conversation thread state, recent turns, focused resources, and assistant-level memory handles
- `AssistantContext`: runtime services for a single turn or agent loop iteration
- `AgentLoop`: the control loop that decides whether to answer directly, invoke a local application capability, invoke a skill, or invoke an MCP-backed capability
- `CapabilityRegistry`: a unified registry for local applications, local tools, skills, and MCP-exposed tools
- `ApprovalManager`: a higher-level approval layer above low-level policy checks, used for user-facing confirmations

The framework now treats `desktop_content_application` as the first built-in capability, not as the final shell for the whole assistant.

## Capability System

The assistant platform should standardize capabilities as first-class components. A capability is anything the agent loop can deliberately choose to use to advance the task.

Phase 1 should support these capability classes:

- local desktop application capabilities
- local deterministic tools
- skills
- MCP-backed tools or services

This should be expressed through a common interface such as:

- `CapabilitySpec`
- `CapabilityInvocation`
- `CapabilityResult`
- `CapabilityProvider`

The important design rule is that skills and MCP integrations should not be bolted directly into `desktop.py` or any single application. They should register through the same capability registry that local desktop capabilities use.

## Skills and MCP

Skills should be modeled as structured assistant capabilities rather than free-form prompt snippets. A skill should be able to declare:

- trigger metadata
- required context
- optional planner hints
- tool or capability dependencies
- output contract

MCP integration should be modeled as an external capability provider. The framework should not hardcode one MCP server flow into the assistant loop. Instead, it should allow MCP providers to expose capabilities into the registry with metadata describing name, schema, safety level, and invocation contract.

This leads to a layered capability selection order:

1. direct answer
2. local desktop capability
3. skill-mediated capability
4. MCP capability

The order can evolve later, but the architecture should assume all four will coexist.

## Resources

The first-stage resource model includes:

- `FileResource`
- `DirectoryResource`
- `DocumentChunkResource`
- `ResourceRef`

Resources are loaded through `ResourceRepository` and resolved through `ResourceResolver`. Tools and application steps should pass `ResourceRef` values rather than raw paths.

## Memory and Policy

The memory model is split into three layers:

- `SessionMemory`
- `WorkingMemory`
- `IndexMemory`

The policy model uses these permission levels:

- `metadata_read`
- `content_read`
- `safe_write`
- `destructive_write`

`SimpleDesktopPolicy` allows reads, requires confirmation for safe writes, and denies destructive writes by default.

For the assistant runtime, policy should be complemented by approval orchestration. Low-level policy still decides what is allowed. The assistant runtime should decide when to interrupt the loop and request user confirmation in a way that is visible and resumable at the session level.

## Phase 1 Scope

The first assistant-runtime milestone should deliver:

- a single-agent assistant loop
- session and turn state management
- a unified capability registry
- local desktop capability integration
- skill registration and invocation slots
- MCP provider registration and invocation slots
- approval-aware execution flow

It should explicitly not deliver:

- multi-agent delegation
- distributed orchestration
- autonomous background swarms
- graphical desktop UI

This keeps the architecture aligned with a Codex-style agent experience while avoiding premature complexity.
