# Next Phase Agent Roadmap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current project from a runnable single-agent framework skeleton into a truly usable desktop agent runtime with execution closure, memory, tool governance, and an extensible capability platform.

**Architecture:** Keep the current capability-first structure, but shift the next phase from "adding more concepts" to "closing the execution loop". Prioritize write actions, artifacts, memory, tool runtime, and recoverability first; then strengthen skills, MCP, selector/ranking, and persona/profile; finally move into workflow and multi-agent style orchestration.

**Tech Stack:** Python dataclasses and stdlib services, existing assistant/application/model abstractions, React + TypeScript frontend shell, current desktop demo server.

## Current Project Position

The project already has a valid platform direction:

- assistant loop and resumable approval skeleton
- capability-first registration model
- desktop content application chain
- initial model router and provider layer
- basic skill and MCP integration slots
- demo UI and streaming chat path

But the current weak point is also clear:

- desktop actions still mainly stop at `list/read/summarize`
- memory is still shallow and session-scoped
- skills are still closer to metadata + trigger phrases than true plugins
- MCP is still a thin adapter instead of a managed subsystem
- tool execution, file mutation, artifact output, and recovery are not yet fully closed-loop

So the next stage should not start with multi-agent orchestration. It should start with making the single agent complete, controllable, and durable.

## Priority Order

### P0: Make The Single Agent Truly Work

This is the highest-priority phase. It should produce a desktop agent that can safely perform work, leave usable outputs, and resume interrupted execution.

#### 1. File Mutation System

Add support for:

- create file
- edit file
- rename/move file
- delete file

But do not implement these as isolated handlers only. They must be delivered together with:

- change intent normalization
- diff preview
- approval before execution
- rollback/checkpoint strategy
- post-change summary

Completion standard:

- a user can ask the agent to modify workspace files
- risky actions are previewed and confirmed before execution
- the framework can explain exactly what changed

#### 2. Artifact System

Introduce a unified artifact/output layer for:

- plans
- reports
- summaries
- generated files
- execution logs
- patches / diffs

Why this matters:

- current agent turns produce answers, but not durable work products
- later skills, memory, and workflows all need stable artifacts to build on

Completion standard:

- every meaningful task can emit structured artifacts
- artifacts can be referenced, reviewed, and reused by later steps

#### 3. Memory Upgrade

Expand memory into distinct layers:

- session memory: current conversation and current focus
- working memory: run-scoped temporary state
- long-term memory: reusable user/workspace preferences and stable facts
- workspace index memory: summaries, embeddings, file-derived cached knowledge

This phase should also define:

- what gets written into memory
- when retrieval happens
- how stale memory is invalidated
- how memory affects planning and selection

Completion standard:

- the agent remembers more than the last focused resource
- memory influences follow-up actions and answer quality
- memory has clear write/read boundaries instead of ad hoc storage

#### 4. Real Tool Runtime

Upgrade tools from registry objects into governed execution units with:

- schema validation
- execution context
- permission class
- timeout/retry policy
- result normalization
- tracing and audit record

This is important because built-in tools, skills, file operations, and MCP tools should converge toward one execution model.

Completion standard:

- tool calls are observable and policy-controlled
- tools are no longer just callable functions attached to metadata

#### 5. Recoverability

Go beyond approval resume and support:

- task checkpoints
- resumable interrupted runs
- explicit failed-step state
- replay / retry of a task segment

Completion standard:

- long-running or interrupted tasks do not fully disappear after failure
- the user can continue a partially completed task instead of restarting from zero

### P1: Make The Platform Extensible

After P0, the runtime should be strong enough to support stable extension points.

#### 6. Skill Plugin System

Upgrade skills into a real plugin model with:

- manifest/schema
- versioning
- dependency declaration
- required capabilities/tools
- permission declaration
- install / enable / disable lifecycle

The key design requirement is that skills should not remain a side-channel trigger mechanism. They should become first-class capability packages.

Completion standard:

- a skill can be discovered, loaded, validated, and governed
- skill execution is not just phrase matching plus a runner callback

#### 7. MCP Lifecycle Management

Strengthen MCP support with:

- session-level provider management
- connection lifecycle
- schema caching
- health monitoring
- auth state
- failure recovery and backoff

Completion standard:

- MCP tools can be treated as managed dependencies
- unavailable providers are visible to selector/planner logic

#### 8. Capability Ranking And Routing

Enhance capability selection with inputs beyond prompt text:

- user preference
- historical success rate
- cost/latency budget
- tool availability
- dependency readiness
- risk class

Completion standard:

- the selector chooses based on platform state, not only descriptive metadata
- routing becomes explainable and tunable

#### 9. Agent Profile / soul.md System

Do not implement `soul.md` as just another large prompt. Build it as a structured profile layer containing:

- identity/persona
- behavior policy
- response style preferences
- planning preferences
- memory preferences
- tool-use rules

It can still be rendered into prompt fragments, but the source of truth should be structured and governable.

Completion standard:

- profile changes can affect planner/selector/responder behavior in a controlled way
- persona is configurable without polluting core runtime logic

### P2: Make The System More Powerful

Only after the earlier phases are stable should the project invest in more complex orchestration.

#### 10. Workflow And Automation Layer

Add:

- reusable workflows
- task templates
- batch operations
- automation triggers
- directory/watcher-based execution

This turns the agent from reactive chat into a repeatable work engine.

#### 11. Evaluation And Observability

Add:

- execution trace inspection
- replay tools
- benchmark tasks
- success/failure analytics
- memory hit/miss visibility
- tool failure rate metrics

This is required before any serious scaling of skills or multi-agent behavior.

#### 12. Agent Re-Orchestration / Multi-Agent

Only then should you revisit "Agent 重新编排". At that point the system will have:

- stable artifacts
- resumable tasks
- governed tools
- usable memory
- explainable capability routing

Without those, multi-agent work would mostly multiply confusion.

The right entry point later is likely:

- planner agent
- executor agent
- reviewer agent

Not an unconstrained swarm.

## Recommended Execution Sequence

Use this order for the next several iterations:

1. File mutation + approval + diff preview
2. Artifact system
3. Memory layering and retrieval rules
4. Tool runtime unification
5. Recoverability and checkpointing
6. Skill plugin system
7. MCP lifecycle management
8. Capability ranking
9. Agent profile / soul.md
10. Workflow / automation
11. Evaluation / observability
12. Multi-agent orchestration

## What Not To Do First

Avoid making these the immediate next goal:

- multi-agent swarm
- broad MCP expansion before lifecycle governance exists
- prompt-only `soul.md`
- adding many tools without approval/audit/runtime discipline
- long-term memory without invalidation and retrieval rules

These directions feel productive but will increase conceptual complexity faster than platform reliability.

## Suggested Near-Term Milestones

### Milestone A: Writable Desktop Agent

Focus:

- file creation/edit/delete
- diff preview
- safe approval flow
- execution summary

Expected outcome:

- the demo stops being read-only and becomes a true workspace operator

### Milestone B: Durable Agent Runtime

Focus:

- artifact store
- memory layering
- resumable tasks

Expected outcome:

- the agent can carry work across turns and interruptions

### Milestone C: Extensible Capability Platform

Focus:

- skill plugins
- MCP lifecycle
- capability ranking
- structured agent profile

Expected outcome:

- the platform becomes a controlled extension system instead of a collection of hooks

## Completion Signal For This Phase

You can consider the next phase successful when all of the following become true:

- the agent can safely mutate workspace files
- each task can leave structured, reviewable outputs
- memory is layered and actually used during planning/execution
- tools, skills, and MCP capabilities are governed under one runtime model
- interrupted tasks can be resumed
- capability routing uses real runtime state

At that point, re-orchestration and more advanced agent topologies will become worth doing.
