# Agent Runtime Framework

`agent-runtime-framework` 当前的主产品路径已经从“单个 `WorkspaceBackend` 顶层运行时”升级为“**Task Graph / Workflow Runtime** 顶层运行时”。当前迁移状态是 **partial migration complete**：`WorkflowRuntime` 已经是 workspace 请求的唯一顶层执行内核，`WorkspaceBackend` / `WorkspaceAgentLoop` 仅保留为图节点下的兼容执行器。

当前生效的主链路是：

- `agent_runtime_framework.demo.server`
- `agent_runtime_framework.demo.app`
- `agent_runtime_framework.workflow.*`
- `agent_runtime_framework.agents.*`（agent definitions / registry / workspace backend）

这意味着仓库现在以 **workflow-first** 的方式处理工作区任务，尤其是复合请求，例如：

- 列目录并总结仓库结构
- 读取 README / 代码文件并汇总
- 把多个子任务结果聚合成最终回答
- 为高风险节点提供审批 / 恢复
- 为长链执行保留运行状态与恢复点

## Current Architecture

当前生效的运行时可以分成五层：

- `agent_runtime_framework.workflow`
  - `GoalSpec / SubTaskSpec / WorkflowRun / WorkflowNode / NodeState`
  - goal analysis / decomposition / graph builder
  - scheduler / runtime / approval / persistence
  - aggregation / final response / native node executors

- `agent_runtime_framework.agents`
  - 兼容单子任务执行器
  - planner / evaluator / tool execution / answer synthesis
  - 作为 workflow node 的兼容 backend，而不是顶层主运行时

- `agent_runtime_framework.demo`
  - 本地 HTTP server
  - demo app shell
  - model center wiring
  - conversation routing + workflow runtime entry

- `agent_runtime_framework.tools` / `resources` / `memory` / `models`
  - tool registry / tool execution
  - workspace resource resolution
  - layered memory persistence
  - model routing and provider registration

## Current Entry Point

当前 demo 前后端主路径为：

`frontend -> demo/server.py -> create_demo_assistant_app() -> DemoAssistantApp -> WorkflowRuntime`

其中：

- **compound / multi-step workspace goals** 走 workflow 主路径
- **conversation-style requests** 仍然走 conversation routing
- `WorkspaceBackend` 仍然保留，但定位是 **compatibility execution backend**，而不是顶层唯一运行时

## Migration Status

当前 graph-first 迁移处于 **partial migration complete** 阶段。规则已经收口为：**workspace 请求的顶层执行内核只有 `WorkflowRuntime`；`WorkspaceBackend` 与 `WorkspaceAgentLoop` 仅作为 compatibility executor 存在，不能再被视为产品入口运行时。**

| Area | Current | Target |
| --- | --- | --- |
| routing | graph-native | graph-native |
| graph build | graph-native | graph-native |
| approval / resume | graph-native | graph-native |
| aggregation | graph-native | graph-native |
| final response | graph-native | graph-native |
| complex workspace subtask execution | loop-backed compatibility | explicit graph nodes first, loop fallback only |
| clarification handling | partially loop-backed | graph-native first |
| tool-call orchestration fallback | loop-backed compatibility | explicit graph nodes first |

## Workflow Runtime Status

当前已经落地的 workflow 纵向切片包括：

- workflow domain models
- sequential scheduler + runtime loop
- deterministic goal analysis / decomposition
- deterministic graph builder
- native `workspace_overview` / `file_read` executors
- `CodexSubtaskExecutor` compatibility adapter
- aggregation / final response executors
- node-level approval / resume
- file-backed workflow persistence
- demo app 对 compound goal 的 workflow-first 路由
- top-level public exports: `WorkflowRuntime`, `WorkflowRun`, `WorkflowNode`, `WorkflowGraph`

当前迁移已经完成的部分主要是：

- `WorkflowRuntime` 作为 workspace 请求的唯一顶层执行内核
- 非 chat workspace 请求统一先进入 workflow runtime
- clarification follow-up 优先回到 workflow path，而不是 app 层直连 loop
- graph-native approval / resume / aggregation / final response
- `tool_call` / `clarification` 的首批显式 workflow executors
- `target_resolution` / `file_inspection` / `response_synthesis` 的第二批 graph-native executors
- `workspace_subtask` bridge 的 `fallback_reason` / `compatibility_mode` / `source_loop` 元数据

当前仍保留为兼容 fallback 或后续增强的部分主要是：

- 更丰富的 graph-native node taxonomy
- 并行调度与更细粒度的子任务图
- model-planned graph 的进一步扩展
- subagent / MCP / skills 级别的图节点化

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

The demo combines general conversation routing with the workflow runtime, compatibility Codex execution, workspace tools, layered memory, and model-center-based per-role model routing.

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

**Tests (optional):** `pip install -e "[dev]"` then `pytest`.

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

以下文档描述的是当前仍然有效或正在执行的结构：

- `docs/当前Agent设计框架.md`
- `docs/2026-03-31-TaskGraph工作流引擎重构设计方案.md`
- `docs/plans/2026-03-31-task-graph-workflow-engine-implementation.md`

当前工作区里部分早期设计文档已经被移除或处于删除状态；不要再把它们视为当前主架构说明。
## Five-Layer Agent Stack

The target architecture is organized as:

- Entry Trigger Layer
- AgentTool Orchestration Layer
- Agent Definition Layer
- Runtime Execution Layer
- Supporting Capability Layer

`WorkflowRuntime` remains the execution kernel. `WorkspaceBackend` is a backend executor. `skills` and `MCP` are reserved as future extension interfaces through the agent definition and orchestration layers rather than being hard-coded into the demo app.

