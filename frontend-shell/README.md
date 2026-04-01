# Desktop Assistant Shell

这是 `agent-runtime-framework` 的前端壳骨架，目标技术栈是：

- `React`
- `Vite`
- `Electron`

当前状态（正式前端）：

- 已经接好现有 demo API 协议：`/api/session`、`/api/chat`、`/api/approve`
- 已经接好统一模型中心协议：`GET /api/model-center`、`POST /api/model-center`、`POST /api/model-center/actions`
- 已经接好流式聊天协议：`/api/chat/stream`
- 已经具备 Electron 主进程与 preload 骨架
- 可以先作为 Web 壳开发，也可以直接作为 Electron 壳运行
- 前端已经拆成 `Chat / History / Settings` 三个分层视图

## 先作为 Web 壳运行

先启动 workspace backend API + UI server：

```bash
cd /Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework
python -m agent_runtime_framework.demo.server --workspace .
```

再在本目录运行：

```bash
npm install
npm run dev
```

默认会把 `/api/*` 代理到 `http://127.0.0.1:8765`；生产态则由 Python server 直接托管构建产物。

## 作为 Electron 壳运行

先确保 assistant demo API 已经在跑，然后在本目录运行：

```bash
npm install
npm run dev
```

当前 Electron 会在开发态加载 `http://127.0.0.1:3000`，生产态加载 `dist/index.html`。
开发脚本已经固定要求 Vite 监听 `127.0.0.1:3000`，并在 Electron 启动前等待该地址的 HTTP 响应。
