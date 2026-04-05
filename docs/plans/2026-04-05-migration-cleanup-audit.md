# Migration Cleanup Audit

本文件冻结 2026-04-05 时点上仍然存在的迁移期债务，用来约束后续 cleanup 不再只停留在“方向正确”，而是收口到可删除、可验证、可追踪的边界。

## Debt Inventory

### 1. `AgentGraphRuntime` 职责仍然过重

当前 `agent_runtime_framework/workflow/agent_graph_runtime.py` 同时承担了：

- orchestration loop
- persisted state restoration
- persisted subrun restoration
- system node materialization
- finalize / judge 后处理

目标边界：

- `AgentGraphRuntime` 只保留 orchestration
- state restore 下沉到单独 helper / store
- system-node materialization 下沉到单独 helper

### 2. compatibility bridge 仍由 `DemoAssistantApp` 持有

当前 `agent_runtime_framework/demo/app.py` 仍包含 `_run_workspace_subtask()`，并直接组装：

- `TaskState`
- `WorkspaceAction`
- `WorkspaceTask`
- `EvidenceItem`

这说明 compatibility subtask bridge 仍然是 app-owned 逻辑，而不是 runtime-owned 或 bridge-owned 逻辑。

目标边界：

- app 只负责 wiring / session / payload
- compatibility bridge 由独立 runner 持有

### 3. steady-state graph flow 仍有 scheduler bypass

当前 `AgentGraphRuntime` 里仍存在 direct executor calls，例如对：

- `clarification`
- `evidence_synthesis`
- `final_response`

这些路径直接调用 `workflow_runtime._execute(...)`，没有完整经过 scheduler 驱动的 graph execution。

目标边界：

- steady-state flow 中的节点执行统一经过 `GraphExecutionRuntime`
- direct executor call 仅允许保留在极小且有注释的过渡 fallback 中，最好完全移除

### 4. `workflow` 层仍反向依赖 `agents.workspace_backend`

当前 `workflow` 下多个模块仍直接 import：

- `agents.workspace_backend.prompting`
- `agents.workspace_backend.run_context`
- `agents.workspace_backend.models`

这使得“workflow 是主运行时，workspace backend 是 compatibility backend”的边界在代码层不干净。

目标边界：

- planner-time parsing / prompt assembly helper 收回到 `workflow`
- compatibility-specific models 只在 bridge executor 处依赖

### 5. bridge-path 测试覆盖仍然偏弱

虽然现有 `tests/test_demo_app.py` 和 `tests/test_workflow_codex_subtask.py` 已经覆盖了一部分 workflow bridge 行为，但还没有保护下面这个已确认的失败模式：

- `DemoAssistantApp._run_workspace_subtask(..., metadata={"target_path": "README.md"})`
- 因缺少 `EvidenceItem` 导入而抛出 `NameError`

目标边界：

- bridge-path bug 必须先被直接测试锁定
- 后续 bridge extraction 也要继续保留这条 regression coverage

## Cleanup Rules

后续 migration cleanup 需要遵守以下规则：

- `RootGraphRuntime` 只负责 route，不负责 business logic
- `AgentGraphRuntime` 只负责 orchestration，不手工执行业务节点
- `GraphExecutionRuntime` 只负责 scheduler-driven execution
- compatibility bridge executor 是过渡实现，不承载 app-specific 逻辑
- direct executor calls outside scheduler/runtime 一律视为迁移债
- `workflow` 对 `agents.workspace_backend` 的 planner-time 反向依赖需要被移除
