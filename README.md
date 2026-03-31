# Agent Runtime Framework

`agent-runtime-framework` currently ships a **single active product/runtime path**:

- `demo/server.py`
- `demo/app.py`
- `agents/codex/*`

In other words, the repository now centers on a **Codex-style action-centric agent runtime** for workspace tasks such as:

- listing directories
- reading and summarizing files
- explaining repositories and modules
- editing workspace files
- running verification commands
- persisting layered memory for target resolution and follow-up work

The older generic `assistant runtime` and `desktop application` chains have been removed from the active codebase.

## Current Architecture

The live runtime is composed of:

- `agent_runtime_framework.agents.codex`
  - task semantics
  - planning
  - tool execution
  - evaluation
  - answer synthesis
  - layered memory and resolver hint policy

- `agent_runtime_framework.demo`
  - local HTTP server
  - demo app shell
  - model center wiring

- `agent_runtime_framework.tools`
  - Codex tool registry and execution

- `agent_runtime_framework.resources`
  - workspace resource resolution and semantics

- `agent_runtime_framework.memory`
  - session memory
  - index memory
  - markdown-backed memory persistence

- `agent_runtime_framework.models`
  - provider registration
  - auth sessions
  - model routing

## Current Entry Point

The demo frontend/backend path is:

`frontend -> demo/server.py -> create_demo_assistant_app() -> DemoAssistantApp -> CodexAgentLoop`

There is no separate application-runner product path anymore.

## Memory V2

The current Codex runtime uses an OpenClaw-style layered memory design:

- low-confidence observations stay in daily memory
- resolver-eligible memory is explicitly marked
- entity bindings such as `README -> README.md` are stored separately
- target resolution no longer trusts low-information directory summaries

See:

- [docs/plans/2026-03-31-memory-v2-openclaw-style.md](docs/plans/2026-03-31-memory-v2-openclaw-style.md)
- [docs/2026-03-30-Agent整体重构设计方案-已完成.md](docs/2026-03-30-Agent整体重构设计方案-已完成.md)

## Demo Backend (Python)

The demo HTTP API and bundled web UI live in `agent_runtime_framework.demo.server`. The server is implemented with the standard library (`ThreadingHTTPServer`); there is no separate ASGI process (for example, no uvicorn).

**Requirements:** Python **3.10+** (the package uses `@dataclass(slots=True)` and similar 3.10+ APIs).

From the repository root, install the package in editable mode so the console script and imports resolve:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Start the demo (pick one):

```bash
arf-desktop-demo --workspace .
```

```bash
python -m agent_runtime_framework.demo.server --workspace .
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

**CLI options:** `--workspace` (default `.`), `--host` (default `127.0.0.1`), `--port` (default `8765`).

**Config:** the model center persists settings under `<workspace>/.arf_demo_config.json` (created or updated through the UI/API). On first use it seeds a DashScope-compatible layout:

- provider instance: `dashscope`
- base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- default routed model: `qwen3.5-plus`

You still need a valid API key (or other credentials) for that provider in the model center before remote calls succeed.

The demo combines general conversation routing with the Codex agent loop, workspace tools, layered memory, and model-center-based per-role model routing.

**HTTP surface (current):**

- **GET** `/` — bundled demo HTML
- **GET** `/app.js`, `/styles.css` — static assets
- **GET** `/api/session` — workspace, session, plan/run history, memory, context
- **GET** `/api/model-center` — model center snapshot
- **POST** `/api/chat` — non-streaming chat
- **POST** `/api/chat/stream` — Server-Sent Events stream
- **POST** `/api/approve` — approval resume (`token_id`, `approved`)
- **POST** `/api/replay` — replay by `run_id`
- **POST** `/api/context` — switch agent profile / workspace
- **POST** `/api/model-center` — update model center payload
- **POST** `/api/model-center/actions` — model center actions (`action` + body)

**Tests (optional):** `pip install -e ".[dev]"` then `pytest`.

## Frontend Shell

An `Electron + React + Vite` shell lives in [frontend-shell](frontend-shell). It proxies **`/api`** to the Python demo (default target `http://127.0.0.1:8765`). Override with `VITE_ASSISTANT_API_BASE` if the backend uses another host or port.

**Important:** `npm run dev` starts **both** the Vite dev server and Electron (`concurrently`). Vite binds **127.0.0.1:3000** (strict port).

For a **browser-only** dev server (no Electron window):

```bash
cd frontend-shell
npm install
npm run dev:web
```

Then start the Python demo separately so `/api` can be proxied.

The scaffold includes an Electron main process and preload bridge so it can grow into a desktop shell without Tauri.

## Documentation Status

The following documents describe the **current** active architecture:

- [docs/2026-03-30-Agent整体重构设计方案-已完成.md](docs/2026-03-30-Agent整体重构设计方案-已完成.md)
- [docs/当前Agent设计框架.md](docs/当前Agent设计框架.md)
- [docs/当前进展与改进建议.md](docs/当前进展与改进建议.md)

Some older docs under `docs/` and `docs/plans/` still describe the removed generic assistant/application chains. Those files are now historical references unless they explicitly say otherwise.
