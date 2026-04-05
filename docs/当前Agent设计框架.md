# 当前 Agent 设计框架

> 详细架构说明见 `docs/architecture/final-agent-graph-runtime.md`。

## 1. 当前主链路

当前代码库的 workspace 请求统一走 graph-first 主链路：

`用户输入 -> RootGraphRuntime -> AgentGraphRuntime -> GraphExecutionRuntime -> Judge / Final Response`

其中：

- conversation 请求仍走 graph 结构，但使用轻量 conversation branch
- workspace 请求走 `AgentGraphRuntime` 的迭代式子图编排
- 所有节点执行都由 `GraphExecutionRuntime` 调度
- approval / resume / clarification / final response 都挂在图执行语义上

## 2. 责任边界

当前责任边界已经收口为：

- `RootGraphRuntime`：只负责 route decision
- `AgentGraphRuntime`：只负责 iterative graph orchestration
- `GraphExecutionRuntime`：只负责 scheduler-driven node execution
- `DemoAssistantApp`：只负责 app/session/payload 组织
- `DemoRuntimeFactory`：只负责 wiring 和服务装配
- `workspace_subtask`：是唯一明确保留的兼容 bridge

## 3. 当前稳定能力

当前已稳定具备的能力包括：

- goal analysis / decomposition
- workspace discovery / content search / chunked file read
- evidence synthesis / aggregation / final response
- clarification / approval / resume
- workflow persistence / replay
- target resolution
- `workspace_subtask` fallback 元数据暴露

当前稳定使用的核心对象包括：

- `WorkflowRun`
- `WorkflowGraph`
- `WorkflowNode`
- `WorkflowEdge`
- `NodeState`
- `NodeResult`
- `GoalSpec`
- `SubTaskSpec`

## 4. 当前节点类型

当前主链路实际使用的节点类型包括：

- `workspace_discovery`
- `content_search`
- `chunked_file_read`
- `aggregate_results`
- `evidence_synthesis`
- `verification`
- `approval_gate`
- `final_response`
- `workspace_subtask`
- `target_resolution`
- `clarification`
- `conversation_response`

其中：

- `workspace_discovery` / `content_search` / `chunked_file_read` 组成默认 evidence chain
- `evidence_synthesis` 是统一的证据总结节点
- `final_response` 读取 judge 结果后生成最终回答
- `workspace_subtask` 是兼容 bridge，不代表主路径

## 5. 当前文档范围

本仓库当前只保留描述现行结构的文档：

- `README.md`
- `docs/当前Agent设计框架.md`
- `docs/architecture/final-agent-graph-runtime.md`
- `docs/architecture/agent-stack-target.md`
