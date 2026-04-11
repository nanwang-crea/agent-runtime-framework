# 当前 Agent 设计框架

> 目标分层见 `docs/分层设计.md`。本文描述的是当前代码中已经落地的主链路与责任边界。

## 1. 当前主链路

当前 workspace 请求已经从单纯的 `node-first` 规划演进为：

`用户输入 -> GoalEnvelope -> Judge/Planner 上下文 -> recipe/capability selection -> recipe expansion -> GraphExecutionRuntime -> Judge / Final Response`

其中：

- conversation 请求仍走 graph 结构，但使用轻量 conversation branch
- workspace 请求仍由 `AgentGraphRuntime` 负责多轮迭代
- `subgraph_planner` 的主输出已经优先是 `selected_recipe_id` / `selected_capability_ids`
- recipe/capability 选择后，会展开为 `PlannedNode` + `WorkflowEdge`
- 实际执行仍由 `GraphExecutionRuntime` 调度原子节点
- approval / resume / clarification / verification / final response 继续挂在图执行语义上
- 为兼容模型波动，planner 仍保留 legacy `nodes` 契约回退，但不再是首选路径

## 2. 分层边界

当前系统按以下方式分层：

- `api/app.py` / `api/server.py`：FastAPI 入口与启动
- `api/routes/*`：HTTP / SSE 适配
- `api/services/chat_service.py`：聊天链路与 workflow 协调
- `api/services/run_service.py`：审批恢复、replay 与运行记录恢复
- `RootGraphRuntime`：route decision
- `AgentGraphRuntime`：迭代式 agent graph orchestration
- `GraphExecutionRuntime`：scheduler-driven node execution
- `capabilities/*`：能力定义、recipe 定义、registry
- `workflow/planning/capability_selection.py`：按目标、judge、diagnosis 选择 recipe / capability chain
- `workflow/planning/recipe_expansion.py`：将 recipe / capability chain 展开成可执行子图
- `workflow/planning/judge.py`：判断是否收敛、缺什么能力、下一轮应偏向哪条 recipe/capability 路线
- `workflow/nodes/*`：原子节点与 graph-native 写节点执行

## 3. 当前语义层

当前已稳定引入两层语义抽象：

### 3.1 Capability

`CapabilitySpec` 现在表达任务能力，而不只是 executor 列表。核心字段包括：

- `capability_id`
- `description`
- `intents`
- `preconditions`
- `produces`
- `toolchains`
- `failure_signatures`
- `verification_recipe`
- `extension_policy`

当前默认能力包括：

- `resolve_target_in_workspace`
- `search_workspace_content`
- `search_workspace_symbols`
- `read_workspace_evidence`
- `edit_workspace_file`
- `move_or_rename_path`
- `delete_workspace_path`
- `run_workspace_verification`
- `inspect_test_failure`

### 3.2 Recipe

`CapabilityMacro` 已演进为任务级 recipe 结构。核心字段包括：

- `recipe_id`
- `intent_scope`
- `entry_conditions`
- `required_capabilities`
- `optional_capabilities`
- `exit_conditions`
- `fallback_recipes`
- `verification_strategy`

当前默认 recipe 包括：

- `resolve_then_read_target`
- `search_then_read_evidence`
- `locate_inspect_edit_verify`
- `inspect_patch_verify_python`
- `resolve_then_move_or_rename`
- `resolve_then_delete_path`

## 4. 规划与诊断

当前 planner / judge / diagnosis 已形成如下闭环：

- planner prompt 优先要求输出 `selected_recipe_id` 与 `selected_capability_ids`
- judge 输出不再只靠 `allowed_next_node_types`，而是优先输出：
  - `preferred_recipe_ids`
  - `blocked_recipe_ids`
  - `preferred_capability_ids`
  - `must_cover_capabilities`
  - `capability_gap`
- `capability_diagnosis` 会同时消费：
  - judge 传下来的 capability / recipe 语义
  - failure diagnosis 中的恢复建议
  - 工具级失败线索
- recovery 仍保留 tool/node 级观测信息，但对 planner 的主要反馈已经提升到 capability / recipe 语义

## 5. 当前原子节点层

当前实际执行的原子节点仍包括：

- `interpret_target`
- `plan_search`
- `plan_read`
- `target_resolution`
- `workspace_discovery`
- `content_search`
- `chunked_file_read`
- `tool_call`
- `clarification`
- `verification`
- `verification_step`
- `capability_diagnosis`
- `capability_extension`
- `aggregate_results`
- `evidence_synthesis`
- `final_response`
- `create_path`
- `move_path`
- `delete_path`
- `apply_patch`
- `write_file`
- `append_text`

这些节点仍然是最小执行单元，recipe/capability 只负责选择与展开，不替代原子执行层。

## 6. 运行时稳定对象

当前主链路稳定依赖的核心对象包括：

- `GoalEnvelope`
- `CapabilitySpec`
- `CapabilityMacro`
- `JudgeDecision`
- `PlannedNode`
- `PlannedSubgraph`
- `WorkflowRun`
- `WorkflowGraph`
- `WorkflowNode`
- `WorkflowEdge`
- `NodeState`
- `NodeResult`
- `AgentGraphState`

## 7. 当前文档范围

本仓库当前描述现行结构的主要文档包括：

- `README.md`
- `docs/当前Agent设计框架.md`
- `docs/分层设计.md`
- `docs/architecture/final-agent-graph-runtime.md`
