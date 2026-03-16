# Agent Runtime Framework Desktop Content Architecture

## Summary

`agent-runtime-framework` now includes a first-stage desktop content architecture on top of the existing graph and runtime core. The framework keeps graph execution generic, while adding four new framework-level capabilities:

- application orchestration
- resource modeling
- layered memory
- permission and policy decisions

The first built-in application is `desktop_content_application`, which supports directory listing, file resolution, content reading, summary generation, and follow-up references based on session memory.
Its intent interpretation is LLM-first when an `llm_client` is available in application context, with a lightweight rule fallback for offline or test-only usage.
Structured stage resolution is shared through `run_stage_parser`, so interpret, resolve, and plan can all follow the same precedence: custom service override, then LLM structured parsing, then deterministic fallback.
Concrete desktop actions are now executed through `DesktopActionHandlerRegistry`, and repository grounding uses `ResolverPipeline`, so desktop-specific behavior is modular without pushing deterministic file operations into the LLM.

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
