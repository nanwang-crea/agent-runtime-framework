# Model Center V2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace scattered model/config/auth endpoints with one unified Model Center API and a single V2 config schema.

**Architecture:** Introduce a dedicated `ModelCenterService` that owns config migration/read/write, provider auth state projection, and role routing updates. Server exposes only `/api/model-center` and `/api/model-center/actions` for model settings. Frontend consumes one state object instead of stitching `/api/models` + `/api/config`.

**Tech Stack:** Python stdlib HTTP server, dataclasses, existing model/provider abstractions, React + TypeScript.

### Task 1: Add Model Center V2 domain + migration

**Files:**
- Create: `agent_runtime_framework/demo/model_center.py`
- Modify: `agent_runtime_framework/demo/config.py`
- Test: `tests/test_model_center.py`

**Step 1: Write failing tests**
- V1 config auto-migrates to V2 with `schema_version=2`.
- Missing providers are seeded (`dashscope`, `minimax`, `codex_local`).
- Routes are normalized to `{provider, model}` keys.

**Step 2: Run failing tests**
- `pytest -q tests/test_model_center.py -k migration`

**Step 3: Implement minimal migration/store/service**
- Add V2 schema helpers and migration function.
- Add `ModelCenterService` read/update/action methods.

**Step 4: Run tests**
- `pytest -q tests/test_model_center.py -k migration`

### Task 2: Switch backend API to unified endpoints

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/demo/server.py`
- Modify: `tests/test_demo_app.py`

**Step 1: Write failing tests**
- `model_center_payload()` returns unified structure.
- `update_model_center()` persists routes/providers in V2.
- action endpoint updates auth state.

**Step 2: Run failing tests**
- `pytest -q tests/test_demo_app.py -k model_center`

**Step 3: Implement minimal backend changes**
- Replace models/config methods with model-center methods.
- Replace routes:
  - `GET /api/model-center`
  - `PUT /api/model-center`
  - `POST /api/model-center/actions`
- Remove old model/config API handlers.

**Step 4: Run tests**
- `pytest -q tests/test_demo_app.py -k model_center`

### Task 3: Switch frontend to unified API and state

**Files:**
- Modify: `frontend-shell/src/types.ts`
- Modify: `frontend-shell/src/api.ts`
- Modify: `frontend-shell/src/App.tsx`

**Step 1: Write failing TS build checks (existing compilation path)**
- Ensure no references remain to removed API types (`ModelsResponse`, `ConfigResponse` in settings flow).

**Step 2: Implement minimal frontend changes**
- Add `ModelCenterResponse` type.
- Replace fetch/update/auth/select calls with:
  - `fetchModelCenter`
  - `updateModelCenter`
  - `runModelCenterAction`
- Refactor settings state to single source `modelCenter`.

**Step 3: Run checks**
- `npm --prefix frontend-shell run build`

### Task 4: Regression tests and cleanup

**Files:**
- Modify: `tests/test_demo_app.py`
- Modify: `tests/test_models.py` (only if needed)
- Modify: `README.md` (API section)

**Step 1: Add coverage**
- Confirm model list includes qwen + minimax + codex under unified payload.
- Confirm qwen remains selectable after migration.

**Step 2: Run full tests**
- `pytest -q`

**Step 3: Validate frontend build**
- `npm --prefix frontend-shell run build`

