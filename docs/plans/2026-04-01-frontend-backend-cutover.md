# Frontend Backend Cutover Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `frontend-shell` the only official frontend for the workspace backend, and have the Python demo server serve that frontend directly with no legacy demo asset layer.

**Architecture:** Keep the API contract under `agent_runtime_framework/demo/server.py`, but switch static asset serving from `agent_runtime_framework/demo/assets` to `frontend-shell/dist` as the authoritative UI bundle. Update tests to validate the official shell instead of legacy `app.js/styles.css/index.html` demo assets.

**Tech Stack:** Python HTTP server, React/Vite/Electron frontend shell, existing `/api/*` contract, pytest.

### Task 1: Replace legacy static asset serving

**Files:**
- Modify: `agent_runtime_framework/demo/server.py`
- Delete: `agent_runtime_framework/demo/assets/*`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing test**
Reuse the existing demo asset test and adjust it to the new shell entry.

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py::test_demo_assets_are_loadable -q`
Expected: FAIL after removing legacy asset names.

**Step 3: Write minimal implementation**
Serve `/` and bundled static files from `frontend-shell/dist`, with SPA fallback to `index.html` for non-API routes.

**Step 4: Run targeted test**
Run: `pytest tests/test_demo_app.py::test_demo_assets_are_loadable -q`
Expected: PASS.

### Task 2: Make frontend-shell the formal UI contract

**Files:**
- Modify: `frontend-shell/README.md`
- Modify: `tests/test_frontend_shell_scaffold.py`
- Modify any backend tests that still reference legacy asset filenames.

**Step 1: Write the failing test**
Reuse existing scaffold assertions and demo server asset assertions.

**Step 2: Run test to verify expected coverage**
Run: `pytest tests/test_frontend_shell_scaffold.py tests/test_demo_app.py -q`
Expected: targeted failures until backend serving is aligned.

**Step 3: Write minimal implementation**
Update docs/tests so `frontend-shell` is treated as the official frontend and `demo/assets` no longer appears in the runtime path.

**Step 4: Run targeted tests**
Run: `pytest tests/test_frontend_shell_scaffold.py tests/test_demo_app.py -q`
Expected: PASS.

### Task 3: Validate the full cutover

**Files:**
- Modify only if validation exposes contract mismatches.

**Step 1: Run full test suite**
Run: `pytest -q`
Expected: PASS.
