# Model Provider Layer Redesign

**Goal:** Rework the model access layer so provider protocol, configured provider instances, runtime auth state, model catalog, and role routing each have one clear owner and one clear data shape.

**Status:** Proposed design

## Background

The current Model Center V2 unified the demo API surface, but the underlying model layer still mixes several responsibilities:

- provider registration is code-driven
- provider configuration is file-driven
- auth state exists both in config and in runtime memory
- model lists exist both in config and in provider registry output
- route consumers use only a subset of roles, while runtime parsers use a different role set

This creates a false impression that the system is fully config-driven. In reality, config only patches a set of pre-registered providers. That mismatch is why the layer feels confusing during extension and debugging.

## Current Problems

### 1. Provider identity is ambiguous

Runtime only recognizes providers registered in code, such as `openai`, `dashscope`, `minimax`, and `codex_local`. However, the config schema allows arbitrary provider keys under `providers`. This means a config entry can look valid and still never become usable if no runtime provider with the same name was registered.

### 2. There is no single source of truth for model metadata

The current config contains `models`, but the runtime catalog is actually derived from `ModelRegistry.list_models()`. The config copy is not authoritative and can drift from the real runtime catalog. This should not be persisted in user config.

### 3. Auth state is persisted even though it is runtime-derived

`auth.status` and `auth.last_error` are written back into the config file. These values are not durable user intent; they are runtime observations. Persisting them makes the config noisy and introduces stale state after restarts or environment changes.

### 4. Role routing is only partially modeled

The config currently routes only `conversation`, `capability_selector`, and `planner`. However, `run_stage_parser()` also resolves roles such as `interpreter`, `resolver`, `executor`, and `composer`. The model layer therefore exposes one routing model in the UI and another routing model in the runtime.

### 5. Provider abstraction mixes driver and instance concerns

`OpenAICompatibleProvider` currently owns:

- the protocol adapter
- the provider name
- the default base URL
- the static model catalog
- auth behavior

This works for demos but does not scale cleanly. A provider driver should describe how to talk to a backend type. A configured provider instance should describe where and how this specific endpoint is used.

## Design Goals

The redesign should enforce these rules:

1. Config stores only user intent and durable settings.
2. Runtime state is derived, not persisted.
3. Provider driver identity is separate from configured provider instance identity.
4. Model catalog has one authoritative source.
5. Route roles are shared by UI, runtime, and tests.
6. Adding a new OpenAI-compatible endpoint should not require inventing a new hardcoded provider class name.

## Non-Goals

- Dynamic remote model discovery in this phase
- Multi-tenant credential storage
- Cost-based automatic routing
- Cross-workspace shared config

## Design Options

### Option A: Keep code-registered providers and tighten validation

This is the smallest change. We could reject unknown provider names in config, remove persisted `models`, and stop writing `auth.status` back to disk.

Pros:

- smallest migration cost
- minimal backend change

Cons:

- still conflates provider type and provider instance
- still makes OpenAI-compatible endpoints awkward to extend

### Option B: Move to provider drivers plus configured provider instances

This is the recommended design. Runtime registers a small set of provider drivers by `type`, such as `openai_compatible` and `codex_cli`. The config then defines provider instances, for example `default_openai`, `dashscope_cn`, or `lab_gateway`, each with a `type` and a connection block. Routes point to instance ids, not driver names.

Pros:

- clean separation of protocol and instance
- aligns with user mental model
- supports multiple OpenAI-compatible endpoints naturally

Cons:

- requires config migration
- requires API and UI reshaping

### Option C: Collapse everything into one runtime-only registry

This would remove most file schema complexity and make the runtime the only authority.

Pros:

- simplest runtime model

Cons:

- poor UX for durable settings
- hard to edit outside the running app
- weak fit for a local config center

## Recommended Architecture

Choose Option B.

The system should be split into five layers.

### 1. Provider Driver Layer

Provider drivers are code-defined and registered at startup.

Examples:

- `openai_compatible`
- `codex_cli`

Responsibilities:

- validate connection and credential schema
- authenticate or prepare runtime client
- expose static model definitions or optional dynamic discovery support
- construct request clients

Drivers do not own user-facing instance ids such as `openai`, `dashscope`, or `openApi`.

### 2. Provider Instance Config Layer

Config file stores provider instances by stable id.

Proposed shape:

```json
{
  "schema_version": 3,
  "provider_instances": {
    "default_openai": {
      "type": "openai_compatible",
      "enabled": true,
      "connection": {
        "base_url": "https://api.openai.com/v1"
      },
      "credentials": {
        "api_key": "..."
      },
      "catalog": {
        "mode": "static",
        "models": ["gpt-4.1-mini", "gpt-4.1"]
      }
    },
    "lab_gateway": {
      "type": "openai_compatible",
      "enabled": true,
      "connection": {
        "base_url": "https://freeapi.dgbmc.top/v1"
      },
      "credentials": {
        "api_key": "..."
      },
      "catalog": {
        "mode": "static",
        "models": ["gpt-4.1-mini"]
      }
    }
  },
  "routes": {
    "conversation": {
      "instance": "lab_gateway",
      "model": "gpt-4.1-mini"
    }
  }
}
```

Notes:

- `provider_instances` replaces `providers`
- `type` maps to a runtime driver
- config does not persist `auth.status`
- config-owned model list exists only as the instance catalog declaration, not as a second top-level shadow map

### 3. Runtime Projection Layer

`ModelCenterService` should build a runtime projection from config plus driver outputs. This projection is returned to the frontend and consumed by runtime execution.

Proposed payload shape:

```json
{
  "config": { "...": "durable settings only" },
  "runtime": {
    "instances": {
      "lab_gateway": {
        "type": "openai_compatible",
        "enabled": true,
        "authenticated": true,
        "auth_error": "",
        "models": [
          {
            "model_name": "gpt-4.1-mini",
            "display_name": "GPT-4.1 Mini"
          }
        ]
      }
    },
    "routes": {
      "conversation": {
        "instance": "lab_gateway",
        "model": "gpt-4.1-mini"
      }
    }
  }
}
```

This removes the current split between `config.models`, `config.auth`, and `catalog.providers/models/routes`.

### 4. Routing Layer

Introduce one shared role list in the model domain.

Proposed roles for phase 1:

- `conversation`
- `capability_selector`
- `planner`
- `interpreter`
- `resolver`
- `executor`
- `composer`

Rules:

- routes always use the same schema: `{instance, model}`
- UI and runtime read the same role list
- unconfigured roles may fall back to a designated default route

### 5. Runtime Resolution Layer

`resolve_model_runtime()` should resolve in this order:

1. explicit route for the requested role
2. default route
3. legacy `context.llm_client / llm_model` fallback during migration only

The legacy fallback should be marked deprecated and removed after the migration window.

## Proposed Data Model

### Domain Types

```python
@dataclass(slots=True)
class ProviderDriverSpec:
    type_name: str

@dataclass(slots=True)
class ProviderInstanceConfig:
    instance_id: str
    type_name: str
    enabled: bool
    connection: dict[str, Any]
    credentials: dict[str, Any]
    catalog: InstanceCatalogConfig

@dataclass(slots=True)
class InstanceCatalogConfig:
    mode: str  # static | discover
    models: list[str]

@dataclass(slots=True)
class RouteConfig:
    instance_id: str
    model_name: str

@dataclass(slots=True)
class RuntimeInstanceState:
    instance_id: str
    type_name: str
    authenticated: bool
    auth_error: str | None
    models: list[ModelProfile]
```

## API Changes

### Keep

- `GET /api/model-center`
- `PUT /api/model-center`
- `POST /api/model-center/actions`

### Change

- `config.providers` becomes `config.provider_instances`
- `catalog` becomes `runtime`
- route payload uses `instance` instead of `provider`

### Action endpoints

Keep `authenticate_provider` temporarily, but rename it to `authenticate_instance` in V3.

## Migration Plan

### V2 to V3 migration rules

1. Move `providers` to `provider_instances`.
2. Treat each old provider key as both the new instance id and the legacy display id.
3. Preserve `type`, `enabled`, `connection`, and `credentials`.
4. Drop persisted `auth` blocks.
5. Convert top-level `models[provider]` into `provider_instances[provider].catalog.models` when no instance catalog exists.
6. Convert `routes[role] = {provider, model}` into `{instance, model}`.
7. Add `default` route if all routed roles currently point to the same instance and model.

### Backward compatibility window

For one schema version, server reads both:

- V2: `providers` and `{provider, model}`
- V3: `provider_instances` and `{instance, model}`

Server always writes V3 after migration.

## Frontend Impact

The settings UI should stop pretending provider ids are protocol ids.

Required changes:

- display instance cards, not provider cards
- show instance type separately from instance id
- allow creating multiple OpenAI-compatible instances
- route selectors bind to instance ids
- auth buttons operate on instance ids

Recommended labels:

- instance id: user-editable identifier
- type: fixed driver type
- endpoint: connection base URL

## Testing Strategy

### Unit tests

- V2 config migrates to V3 correctly
- unknown driver type is rejected
- unknown route instance is rejected
- runtime auth state is not written back to config
- runtime catalog is derived from driver plus instance catalog config

### Integration tests

- demo app can load multiple OpenAI-compatible instances at once
- route selection by instance id resolves correct runtime client
- conversation and stage parser roles consume the same route table
- legacy `context.llm_client` fallback still works during transition

### Frontend checks

- build passes after type changes
- settings page renders instance-based routing
- saving one instance does not mutate unrelated instances

## Rollout Plan

### Phase 1: Domain split

- add V3 config schema and migration
- introduce driver vs instance types
- keep legacy API field aliases internally

### Phase 2: Runtime unification

- switch `ModelCenterService` and `ModelRouter` to instance-based routing
- unify role constants
- mark legacy fallback path deprecated

### Phase 3: UI reshape

- change settings page to instance model
- support create, edit, and remove instance flows

### Phase 4: Cleanup

- remove V2 compatibility readers
- remove persisted auth state
- remove legacy `provider` route field names
- remove `context.llm_client / llm_model` fallback if no longer needed

## Risks

- Migration bugs can silently break model routing if route keys are not normalized carefully.
- If legacy fallback remains too long, engineers may continue bypassing the routing layer.
- Static per-instance model catalogs can still drift unless ownership is clearly documented.

## Open Questions

1. Should instance ids be user-editable after creation, or immutable?
2. Do we want one explicit `default` route now, or keep per-role explicit routing only?
3. Should static model lists remain config-defined for OpenAI-compatible instances, or should they move into driver presets plus optional overrides?

## Recommendation

Implement V3 around driver types plus provider instances. That is the smallest redesign that actually fixes the conceptual model. It preserves the current demo architecture while removing the biggest sources of confusion:

- config no longer pretends to define runtime drivers
- auth no longer pollutes durable config
- model catalog has one authoritative path
- routes align across UI and runtime
- OpenAI-compatible endpoints become natural to add and reason about
