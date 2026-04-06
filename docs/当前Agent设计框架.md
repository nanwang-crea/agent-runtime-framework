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
- graph-native write nodes：负责文件系统与文本编辑的工作流阶段语义

## 3. 当前稳定能力

当前已稳定具备的能力包括：

- model-driven goal analysis / decomposition / graph planning
- workspace discovery / content search / chunked file read
- evidence synthesis / aggregation / final response
- clarification / approval / resume
- workflow persistence / replay
- target resolution
- graph-native write-node 执行结果与 verification 结果暴露

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
- `target_resolution`
- `clarification`
- `conversation_response`
- `create_path`
- `move_path`
- `delete_path`
- `apply_patch`
- `write_file`
- `append_text`

当前稳定使用的 graph-native 写节点类型包括：

- `create_path`
- `move_path`
- `delete_path`
- `apply_patch`
- `write_file`
- `append_text`
- `verification`

其中：

- `workspace_discovery` / `content_search` / `chunked_file_read` 组成默认 evidence chain
- `evidence_synthesis` 是统一的证据总结节点
- `final_response` 读取 judge 结果后生成最终回答
- 节点名称表达 workflow stage intent，不与底层 tool 名称一一对应
- tools 继续保持 fine-grained execution primitives
- 文件系统与文本编辑请求已经不再经过兼容 bridge
- 无模型时不再使用规则 fallback 或摘要 fallback，相关阶段直接报错

## 5. 当前文档范围

本仓库当前只保留描述现行结构的文档：

- `README.md`
- `docs/当前Agent设计框架.md`
- `docs/architecture/final-agent-graph-runtime.md`
- `docs/architecture/agent-stack-target.md`
