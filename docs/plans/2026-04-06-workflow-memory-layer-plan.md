# Workflow 记忆层统一改造计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为当前 workflow 架构补齐统一的记忆层，让 planner、judge、clarification interpreter、semantic planning nodes 等模型决策节点稳定使用历史上下文，而工具层只消费模型给出的结构化结果。

**Architecture:** 本次改造不把“记忆能力”散落在各个工具和执行器里，而是在 workflow state 中建立统一的 `WorkflowMemoryState`，并通过 `memory_views` 将压缩后的记忆上下文注入模型节点。工具层不直接理解复杂历史，只读取模型生成的结构化 target/search/read 结果和必要约束。

**Tech Stack:** Python 3.12、pytest、现有 AgentGraphRuntime、GoalEnvelope、AgentGraphState、shared_state、session/index memory、semantic planning nodes。

## 一、改造目标

当前项目已经具备多种“记忆相关能力”，但这些能力是分散的：

1. `session_memory` 和 `index_memory`
2. `GoalEnvelope.memory_snapshot`
3. `AgentGraphState` 中的失败历史、恢复历史、策略摘要
4. `shared_state` 中的 `interpreted_target`、`search_plan`、`read_plan`

问题不在于“没有记忆”，而在于：

1. 记忆写入点不统一
2. 记忆读取点不统一
3. 模型节点的 prompt 很多没有带上这些记忆
4. 工具节点承担了过多语义判断职责

本次改造的目标是：

1. 建立统一的 workflow 记忆层
2. 所有需要语义判断的节点默认读取记忆视图
3. 工具层不再理解复杂历史，只执行模型给出的结果
4. clarification 不再把用户澄清回复当作一条新任务消息，而是当作对原任务状态的结构化补全

## 二、总体原则

### 1. 记忆优先服务模型决策层

本次改造明确采用以下边界：

1. `planner`、`judge`、`clarification_interpreter`、`interpret_target`、`plan_search`、`plan_read` 等模型节点读取记忆
2. `target_resolution`、`content_search`、`chunked_file_read` 等工具/执行节点不直接消费复杂历史，只消费模型输出的结构化结果
3. `tool_call_executor` 和 `filesystem_node_executors` 只做参数约束和执行，不负责理解历史记忆

### 2. 记忆不是全量对话

传给模型的不是全量消息，而是压缩后的结构化记忆视图：

1. 当前确认目标
2. 已排除候选
3. 最近澄清历史
4. 最近失败原因
5. 最近无效动作
6. 当前 semantic plan

### 3. 工具层只消费结构化结果

工具层只读取以下类型的结果：

1. `interpreted_target`
2. `search_plan`
3. `read_plan`
4. `confirmed_targets`
5. `excluded_targets`

而不直接读取全量澄清历史、失败历史、prompt 上下文。

## 三、统一记忆层设计

### 1. 新增 `WorkflowMemoryState`

建议在 `agent_runtime_framework/workflow/models.py` 中新增：

```python
@dataclass(slots=True)
class WorkflowMemoryState:
    clarification_memory: dict[str, Any] = field(default_factory=dict)
    semantic_memory: dict[str, Any] = field(default_factory=dict)
    execution_memory: dict[str, Any] = field(default_factory=dict)
    preference_memory: dict[str, Any] = field(default_factory=dict)
```

并在 `AgentGraphState` 中新增：

```python
memory_state: WorkflowMemoryState = field(default_factory=WorkflowMemoryState)
```

### 2. 四类记忆子层

#### `clarification_memory`

职责：保存澄清问题与回答上下文。

建议字段：

1. `active_question`
2. `question_type`
3. `candidate_items`
4. `missing_fields`
5. `clarification_history`
6. `last_resolution`

#### `semantic_memory`

职责：保存模型已经解释出的结构化计划。

建议字段：

1. `interpreted_target`
2. `search_plan`
3. `read_plan`
4. `confirmed_targets`
5. `excluded_targets`

#### `execution_memory`

职责：保存运行过程中需要影响下一轮模型决策的摘要。

建议字段：

1. `failure_history`
2. `iteration_summaries`
3. `attempted_strategies`
4. `ineffective_actions`
5. `recovery_history`
6. `conflicts`
7. `quality_summary`

#### `preference_memory`

职责：保存项目级或用户级稳定偏好。

建议字段：

1. `path_preferences`
2. `default_target_bias`
3. `module_aliases`
4. `project_conventions`

## 四、模型层如何使用记忆

这是本次改造的重点。记忆层建立后，必须通过统一的 memory view 注入模型节点。

### 1. 新增 `memory_views.py`

新增文件：

`agent_runtime_framework/workflow/memory_views.py`

提供以下方法：

#### `build_planner_memory_view(state)`

输出给 planner 的压缩视图：

1. `confirmed_targets`
2. `excluded_targets`
3. `open_issues`
4. `ineffective_actions`
5. `recent_failures`
6. `recent_recovery`
7. `search_plan`
8. `read_plan`

#### `build_judge_memory_view(state)`

输出给 judge 的视图：

1. `confirmed_targets`
2. `excluded_targets`
3. `clarification_history`
4. `quality_summary`
5. `conflicts`
6. `semantic_constraints`

#### `build_semantic_memory_view(state)`

输出给 `interpret_target`、`plan_search`、`plan_read` 的视图：

1. `original_goal`
2. `clarification_memory`
3. `semantic_memory`
4. `execution_memory`

#### `build_response_memory_view(state)`

输出给 final response 的视图：

1. `confirmed_targets`
2. `excluded_targets`
3. `verified_facts`
4. `response_constraints`

### 2. 哪些模型节点必须接入 memory view

#### `subgraph_planner.py`

当前已有上下文输入，但需要改成统一读取 `build_planner_memory_view(state)`。

要求：

1. prompt 中默认带 `planner_memory_view`
2. 不再手写拼接零散字段
3. planner 必须看到已确认目标和无效动作

#### `judge.py`

当前 judge 主要看 `aggregated_payload` 和部分质量信号，需要改成同时读取 `build_judge_memory_view(state)`。

要求：

1. judge 能知道用户已经确认过什么 target
2. judge 能知道哪些候选已被排除
3. judge 能把“再次问同一个问题”判断为流程错误，而不是普通歧义

#### `semantic_plan_executors.py`

三个节点必须统一读取 `build_semantic_memory_view(state)`。

要求：

1. `interpret_target` 看到原始目标、上轮候选、澄清回复、失败历史
2. `plan_search` 看到已确认 target、无效检索策略
3. `plan_read` 看到 search_plan、当前缺口、失败历史

#### `final_response`

最终回答应读取 `build_response_memory_view(state)`，避免忽略前面已经明确过的事实和约束。

## 五、工具层如何使用模型输出

本次方案明确：

### 工具层不直接读复杂记忆

工具层不应该直接读：

1. 全量 clarification history
2. 全量 failure history
3. 全量 prior prompt

### 工具层只读结构化输出

#### `target_resolution_executor`

读取：

1. `semantic_memory.interpreted_target`
2. `semantic_memory.confirmed_targets`
3. `semantic_memory.excluded_targets`

#### `content_search_executor`

读取：

1. `semantic_memory.search_plan`
2. `semantic_memory.confirmed_targets`

#### `chunked_file_read_executor`

读取：

1. `semantic_memory.read_plan`
2. `semantic_memory.interpreted_target`

#### `tool_call_executor` / `filesystem_node_executors`

读取：

1. 当前 target 约束
2. 当前写入对象约束

但不读取复杂历史上下文。

## 六、clarification 路径的专项改造

这是优先级最高的部分。

### 当前问题

澄清回复仍然被当成新消息重新分析，导致：

1. 回复整句容易被当成 target hint
2. 用户已经明确过目标，后续还会再次澄清
3. `routing_runtime.py` 中的澄清分支是硬特判，语义不稳定

### 目标方案

把 clarification 改成 continuation，而不是新任务消息。

### 新增组件

#### `ClarificationInterpreter`

输入：

1. 原始用户请求
2. 当前澄清问题
3. 候选项
4. 用户本轮澄清回复
5. 当前 open issues

输出：

1. `resolved_target`
2. `resolved_fields`
3. `remaining_ambiguity`
4. `confidence`
5. `should_reask`
6. `reason`

### 写入策略

clarification 结果要写回：

1. `clarification_memory.last_resolution`
2. `semantic_memory.interpreted_target`
3. `semantic_memory.confirmed_targets`
4. `semantic_memory.excluded_targets`

### 读取策略

后续：

1. planner 看到已确认 target
2. judge 看到这个问题已经回答过
3. target_resolution 按这个约束过滤候选

## 七、统一记忆写入机制

新增文件：

`agent_runtime_framework/workflow/memory_updates.py`

建议提供：

### `remember_clarification(state, ...)`

更新：

1. `clarification_memory`
2. `confirmed_targets`
3. `excluded_targets`

### `remember_semantic_plan(state, ...)`

更新：

1. `interpreted_target`
2. `search_plan`
3. `read_plan`

### `remember_execution_feedback(state, ...)`

更新：

1. `failure_history`
2. `ineffective_actions`
3. `recovery_history`
4. `conflicts`

### `remember_preferences(state, ...)`

更新：

1. `path_preferences`
2. `project_conventions`

## 八、压缩与分层治理

虽然要建立统一记忆层，但进入 prompt 的内容仍需压缩。

### 推荐进入模型的内容

1. 最近 2 次 clarification
2. 最近 2-3 次失败
3. 当前 confirmed/excluded targets
4. 当前 semantic plan
5. 当前 unresolved issues

### 不直接进入模型的内容

1. 全量 shared_state
2. 全量 node_results
3. 全量 reasoning_trace
4. 全量聊天记录

## 九、实施顺序

### 阶段一：建立统一记忆结构

文件：

1. `agent_runtime_framework/workflow/models.py`
2. `agent_runtime_framework/workflow/agent_graph_state_store.py`

任务：

1. 新增 `WorkflowMemoryState`
2. 挂到 `AgentGraphState`
3. 完成序列化/恢复

### 阶段二：建立 memory updates / memory views

文件：

1. 新增 `memory_updates.py`
2. 新增 `memory_views.py`

任务：

1. 统一 memory 写入
2. 统一模型读取视图

### 阶段三：改 planner / judge / semantic nodes

文件：

1. `subgraph_planner.py`
2. `judge.py`
3. `semantic_plan_executors.py`

任务：

1. 让模型节点统一消费 memory view
2. 确保 prompt 默认带记忆

### 阶段四：改 clarification continuation

文件：

1. `routing_runtime.py`
2. `agent_branch_orchestrator.py`
3. 新增 `clarification_interpreter.py`

任务：

1. 去掉澄清回复被当成新任务的旧逻辑
2. 改成 continuation + resolution

### 阶段五：工具层收口

文件：

1. `target_resolution_executor.py`
2. `content_search_executor.py`
3. `chunked_file_read_executor.py`

任务：

1. 工具层只消费模型输出
2. 不再直接读复杂记忆

## 十、验收标准

满足以下条件，可认为本次记忆层改造完成：

1. workflow state 中存在统一的 `WorkflowMemoryState`
2. planner、judge、semantic nodes 的 prompt 默认带记忆视图
3. clarification 回复不再被当成新任务重新分析
4. 用户明确过的 target 在后续节点中可稳定复用
5. 工具层只读取模型给出的结构化结果，而不直接读复杂历史
6. 端到端场景中，澄清后的目标不会再次重复澄清

## 十一、建议结论

当前项目不需要推倒重来。  
更合理的路线是：

1. 保留已有 session/index/graph memory 能力
2. 统一为 workflow 记忆层
3. 让模型节点稳定读取这层记忆
4. 让工具层只消费模型结果

这会比当前“零散 memory + 零散 prompt 注入”稳定得多，也更符合现在已经成型的 graph runtime 架构。
