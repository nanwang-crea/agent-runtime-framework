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
- an assistant runtime layer for single-agent capability selection with skills and MCP slots

Key first-stage modules:

- `agent_runtime_framework.assistant`
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
The assistant runtime provides `AssistantSession`, `CapabilityRegistry`, `AgentLoop`, `SkillRegistry`, MCP provider slots, and approval/resume primitives so desktop capabilities can be composed into a Codex-style single-agent loop.
`AgentLoop` now supports a minimal `plan -> act -> review -> continue/stop` cycle, LLM-first structured capability selection, and resumable approval checkpoints for higher-risk capabilities.
`CapabilitySpec` now carries description, safety level, input contract, cost hint, latency hint, risk class, dependency readiness, and output type metadata, and MCP providers can expose discoverable tool schemas through `MCPToolSpec`.
The framework now also includes a first-stage model access layer with provider registration, auth sessions, model routing, and an OpenAI-compatible provider adapter.

## Demo backend (Python)

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

The demo combines general **conversation** routing with the **Codex-style agent loop** (plan / act / review) and desktop file capabilities where enabled: list/read/summarize files under the workspace, inspect turn and plan history, and use the model center for auth and per-stage model routing.

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

Reference architecture notes live in [docs/desktop-content-architecture.md](docs/desktop-content-architecture.md).
