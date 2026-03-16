# Agent Runtime Framework

`agent-runtime-framework` is a reusable Agent framework package that combines:

- an integrated graph execution module
- reusable Agent runtime abstractions
- tool registration and execution
- policy and memory layers
- resource modeling for local desktop content
- application orchestration for end-to-end assistant workflows
- runtime tracing hooks

The framework now has two entry levels:

- low-level graph/runtime primitives for generic agent execution
- a first-stage desktop content application layer for local files, directories, and document chunks

Key first-stage modules:

- `agent_runtime_framework.graph`
- `agent_runtime_framework.tools`
- `agent_runtime_framework.runtime`
- `agent_runtime_framework.resources`
- `agent_runtime_framework.memory`
- `agent_runtime_framework.policy`
- `agent_runtime_framework.applications`

`agent_runtime_framework.runtime.parse_structured_output` provides a reusable LLM-first structured parsing helper that applications can share instead of embedding prompt + JSON parsing logic locally.
`agent_runtime_framework.applications.run_stage_parser` builds on top of it so application stages can consistently use: service override -> LLM structured parsing -> deterministic fallback.
Desktop-specific deterministic behavior is modularized through `ResolverPipeline` and `DesktopActionHandlerRegistry`.

Reference architecture notes live in [docs/desktop-content-architecture.md](docs/desktop-content-architecture.md).
