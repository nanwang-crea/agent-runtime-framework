# Agent Runtime Framework

`agent-runtime-framework` 当前采用 **Root Graph -> Agent Graph -> Graph Execution Runtime** 的图优先运行时。`RootGraphRuntime` 负责路由，`AgentGraphRuntime` 负责迭代式 agent graph 编排，`GraphExecutionRuntime` 负责节点调度与执行。文件系统与文本编辑请求已经通过 graph-native write nodes 执行，底层继续复用 fine-grained workspace tools。

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

`frontend -> demo/server.py -> create_demo_assistant_app() -> DemoAssistantApp -> RootGraphRuntime -> (conversation graph | AgentGraphRuntime) -> GraphExecutionRuntime`

其中：

- **compound / multi-step workspace goals** 走 `AgentGraphRuntime` + `GraphExecutionRuntime` 主路径
- **conversation-style requests** 仍然走 conversation routing
- `WorkspaceBackend` 作为底层工具与资源访问能力参与执行，不是产品入口运行时
- `DemoAssistantApp` 负责 app/session/payload 组织，不拥有业务执行逻辑

## Runtime Rules

当前运行时规则为：

- `RootGraphRuntime` 只负责 route decision
- `AgentGraphRuntime` 只负责 graph orchestration
- `GraphExecutionRuntime` 只负责 scheduler-driven node execution
- conversation 分支与 workspace 分支都走统一 graph-first 路径
- 写请求已经走 graph-native nodes，底层仍复用 fine-grained workspace tools

| Area | Current | Target |
| --- | --- | --- |
| routing | graph-native | graph-native |
| graph build | graph-native | graph-native |
| approval / resume | graph-native | graph-native |
| aggregation | graph-native | graph-native |
| final response | graph-native | graph-native |
| graph-native write execution | explicit workflow nodes | explicit graph nodes + fine-grained tools |
| clarification handling | partially loop-backed | graph-native first |
| tool-call orchestration fallback | loop-backed compatibility | explicit graph nodes first |
| filesystem writes | graph-native | `create_path` / `move_path` / `delete_path` |
| text edits | graph-native | `apply_patch` / `write_file` / `append_text` |

## Workflow Runtime Status

当前已经落地的 workflow 纵向切片包括：

- workflow domain models
- sequential scheduler + runtime loop
- deterministic goal analysis / decomposition
- native `workspace_overview` / `file_read` executors
- graph-native write-node taxonomy for filesystem and text-edit execution
- aggregation / final response executors
- node-level approval / resume
- file-backed workflow persistence
- demo app 对 compound goal 的 workflow-first 路由
- top-level public exports: `GraphExecutionRuntime`, `WorkflowRun`, `WorkflowNode`, `WorkflowGraph`

当前稳定具备的部分主要是：

- `RootGraphRuntime -> AgentGraphRuntime -> GraphExecutionRuntime` 作为 workspace 请求的唯一主路径
- 非 chat workspace 请求统一先进入 graph-first runtime
- clarification follow-up 优先回到 workflow path，而不是 app 层直连 loop
- graph-native approval / resume / aggregation / final response
- `tool_call` / `clarification` 的首批显式 workflow executors
- `target_resolution` / `file_inspection` / `response_synthesis` 的第二批 graph-native executors
- graph-native write nodes for filesystem and text-edit stages

当前仍作为后续增强项保留的部分主要是：

- richer graph-native node taxonomy on top of the write path
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

- **GET** `/` — bundled frontend entry from `frontend-shell/dist`
- **GET** `/<asset>` — frontend static assets resolved from `frontend-shell/dist`
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

The shell includes an Electron main process and preload bridge; Tauri is not part of the current runtime path.

## Documentation Status

以下文档描述的是当前仍然有效的结构：

- `docs/当前Agent设计框架.md`
- `docs/architecture/final-agent-graph-runtime.md`
- `docs/architecture/agent-stack-target.md`
## Five-Layer Agent Stack

The target architecture is organized as:

- Entry Trigger Layer
- AgentTool Orchestration Layer
- Agent Definition Layer
- Runtime Execution Layer
- Supporting Capability Layer

`RootGraphRuntime` is the route layer. `AgentGraphRuntime` is the orchestration layer. `GraphExecutionRuntime` is the execution kernel. `WorkspaceBackend` is a compatibility backend executor. `skills` and `MCP` are reserved as future extension interfaces through the agent definition and orchestration layers rather than being hard-coded into the demo app.
