# Reflexive Capability Harness 分阶段实施计划

## 文档目的

本文档将 [2026-04-10-reflexive-capability-harness-plan.md](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/docs/plans/2026-04-10-reflexive-capability-harness-plan.md) 拆解为一个可分阶段推进的中文实施计划。

目标不是再次讨论方向，而是回答以下问题：

- 第一阶段到底先做什么
- 每个阶段改哪些模块
- 阶段之间有什么依赖关系
- 每个阶段如何测试和验收
- 哪些内容现在不该做

## 总体实施策略

整体采用四阶段推进：

1. 先补失败分类和恢复契约
2. 再补能力抽象和能力诊断
3. 再补验证配方和能力组合
4. 最后才做受控能力扩展

核心原则：

- 优先补“判断自己为什么失败”
- 其次补“判断自己缺什么能力”
- 再补“恢复后怎么验证真的修好了”
- 最后才开放“自动扩展能力”

## 里程碑总览

### Milestone 1

主题：

- 失败分类与恢复模式收口

目标：

- runtime 能稳定区分主要失败类型
- tool/sandbox/judge 能输出统一恢复信号

交付：

- `FailureDiagnosis`
- `RecoveryMode`
- 结构化错误标签
- planner/judge 上下文接入失败和恢复信息

### Milestone 2

主题：

- 能力视角进入规划主链路

目标：

- runtime 能识别“已有能力没用对”和“确实缺能力”

交付：

- `CapabilitySpec`
- `CapabilityRegistry`
- `capability_diagnosis` 节点
- planner 上下文中的 capability 视图

### Milestone 3

主题：

- 恢复闭环和验证闭环

目标：

- 修改、修复、环境恢复之后都能进入明确验证路径

交付：

- `VerificationRecipe`
- verification policy / recipe registry
- capability macro
- 恢复后验证回流

### Milestone 4

主题：

- 受控能力扩展

目标：

- 只在必要时允许 agent 生成新增能力

交付：

- controlled capability extension pipeline
- 扩展审批与审计机制
- 扩展后的 smoke verification

## 阶段依赖

推进顺序必须遵守以下依赖：

- Milestone 2 依赖 Milestone 1
- Milestone 3 依赖 Milestone 1 和 Milestone 2
- Milestone 4 依赖 Milestone 1、2、3 全部完成

原因：

- 如果没有统一失败分类，能力诊断会变得不稳定
- 如果没有 capability 抽象，verification recipe 会缺少绑定对象
- 如果没有 verification 和治理，自动扩展会很危险

## 实施进度（随仓库滚动更新）

> 与当前 `agent-runtime-framework` 代码库对齐；验收以测试与关键模块为准（2026-04-11）。

### 总览

| 里程碑 | 状态 | 落地要点 |
| :--- | :--- | :--- |
| **Milestone 1** | **已完成** | `workflow/recovery/models`（`FailureDiagnosis` / `RECOVERY_MODES`）、`tools/executor` 与 `sandbox/core` 结构化失败字段、`JudgeDecision` 恢复相关字段、`agent_graph` 的 `recovery_history`/`failure_diagnosis`、`model_context` 的近期失败/恢复视图、回归测试 |
| **Milestone 2** | **已完成** | `capabilities/`（`CapabilitySpec`、`CapabilityRegistry`、默认首批能力）、`workflow/nodes/capability_diagnosis.py`、节点注册表接入、`planner`/`judge` 上下文 `capability_view`（含 `available_capabilities`、`capability_macros`、缺口列表）、`subgraph_planner` 在 judge 给出 `capability_gap`/`preferred_capability_ids` 且路由允许时注入 `capability_diagnosis` |
| **Milestone 3** | **已完成（闭环增强）** | 同上，并新增：`subgraph_planner` 在 `verification_pending`、近期 `recovery_history`（如 `execution_failed` / `repair_environment` / `run_verification`）或 judge `verification_required` 且路由允许时**自动追加** `verification`/`verification_step` 门节点（可用 `services.harness_verification_gate` 关闭）；`VerificationExecutor` 失败输出携带 `on_failure_recovery_mode`（来自 recipe）；`AgentGraphRuntime` 将子图中验证失败信号合并进 `aggregated_payload`；`execution_summary` 增加 `latest_verification_failure_recovery_mode` |
| **Milestone 4** | **已完成（治理增强）** | 同上，并新增：`capability_extension` 默认 **`governance_two_phase=True`**（`execute` 返回 `waiting_approval` + `approval_data.kind=capability_extension`，`resume` 通过后执行 smoke 与审计）；单测/脚本可设 `governance_two_phase=False` 保持单跳行为 |

### 新增或重点测试文件（摘录）

- Milestone 1：`tests/test_tool_registry.py`、`tests/test_error_types.py`、`tests/test_workflow_runtime.py`（等，既有扩展）
- Milestone 2：`tests/test_capability_registry.py`、`tests/test_capability_diagnosis.py`
- Milestone 3：`tests/test_verification_recipes.py`、`tests/test_capability_macros.py`
- Milestone 4：`tests/test_capability_extension.py`
- 门控与扩展：`tests/test_harness_gates.py`

### 刻意未做 / 后续可增强（避免范围失控）

- 不让 agent 自动生成并执行任意 Python / 任意 shell 工具并热加载进核心 runtime
- 能力宏的「自动展开为子图」仍以模型规划为主，代码侧保留诊断注入与契约字段
- 验证门节点当前挂在子图**最后一个计划节点**之后；若子图存在多出口 DAG，可升级为「对所有 sink 追加 gate」或显式 `depends_on` 策略
- **staging registry / promote** 仍未实现：扩展审计已就绪，但「仅写入 staging、再 promote 到正式 registry」需后续数据层与加载器配合

## Milestone 1：失败分类与恢复模式收口

## 阶段目标

让系统先具备“理解失败”的能力。

这一阶段完成后，系统应做到：

- 失败不再只表现为通用字符串错误
- tool 层、sandbox 层、judge 层输出统一的失败分类和恢复模式
- planner 在下一轮能看到最近失败和最近恢复动作

## 本阶段范围

本阶段只做：

- 数据结构
- 错误标签
- 恢复模式契约
- 上下文接入
- 基础测试

本阶段不做：

- capability registry
- capability diagnosis 节点
- capability macro
- 自动生成工具

## 需要新增的数据结构

建议新增文件：

- `agent_runtime_framework/workflow/recovery/models.py`

建议定义：

```python
@dataclass(slots=True)
class FailureDiagnosis:
    category: str
    subcategory: str | None = None
    summary: str = ""
    blocking_issue: str = ""
    recoverable: bool = True
    suggested_recovery_mode: str = ""
    missing_capability: str | None = None
    suggested_capabilities: list[str] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)
```

```python
RECOVERY_MODES = {
    "retry_same_action",
    "repair_arguments",
    "switch_tool",
    "collect_more_evidence",
    "request_clarification",
    "run_verification",
    "repair_environment",
    "compose_capability",
    "extend_capability",
    "handoff_to_human",
}
```

## 需要改造的模块

### 1. `tools/executor.py`

文件：

- [agent_runtime_framework/tools/executor.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/tools/executor.py)

改造目标：

- 为 tool validation error 和 execution error 增加结构化 failure metadata

建议新增输出字段：

- `failure_category`
- `failure_subcategory`
- `suggested_recovery_mode`
- `suggested_tools`
- `suggested_capabilities`

### 2. `sandbox/core.py`

文件：

- [agent_runtime_framework/sandbox/core.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/sandbox/core.py)

改造目标：

- 为 sandbox 拒绝原因提供稳定标签

建议细分：

- `shell_meta_denied`
- `command_not_allowed`
- `network_blocked`
- `path_outside_workspace`
- `read_only_violation`
- `missing_operands`

### 3. `workflow/planning/judge.py`

文件：

- [agent_runtime_framework/workflow/planning/judge.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/workflow/planning/judge.py)

改造目标：

- 扩展 judge contract
- 把 `diagnosis` 和 `strategy_guidance` 进一步结构化

建议新增 judge 输出字段：

- `recommended_recovery_mode`
- `verification_required`
- `human_handoff_required`

### 4. `workflow/runtime/agent_graph.py`

文件：

- [agent_runtime_framework/workflow/runtime/agent_graph.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/workflow/runtime/agent_graph.py)

改造目标：

- 将现有 `execution_failed -> diagnose_and_replan` 细化为结构化恢复记录

建议：

- 在 `recovery_history` 中写入 `failure_diagnosis`
- 在 `iteration_summaries` 中增加 `recovery_mode`
- 在 `execution_summary` 中暴露最近失败分类

### 5. `workflow/context/model_context.py`

文件：

- [agent_runtime_framework/workflow/context/model_context.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/workflow/context/model_context.py)

改造目标：

- 在 planner/judge context 中加入失败与恢复视图

建议新增字段：

- `recent_failure_diagnoses`
- `recent_recovery_modes`
- `ineffective_actions`
- `last_verification_result`

## 本阶段建议实施顺序

1. 定义 `FailureDiagnosis` 和 `RecoveryMode`
2. 改造 `sandbox/core.py` 输出稳定错误标签
3. 改造 `tools/executor.py` 透传错误分类
4. 改造 `judge.py` 输出恢复建议
5. 改造 `agent_graph.py` 持久化恢复结构
6. 改造 `model_context.py` 把相关信息送进 planner/judge
7. 补测试

## 本阶段测试清单

建议新增或扩展测试：

- `tests/test_tool_registry.py`
  - validation error 带 `failure_category`
  - execution error 带 `suggested_recovery_mode`
- `tests/test_error_types.py`
  - sandbox denial reason 稳定输出
- `tests/test_workflow_runtime.py`
  - `recovery_history` 包含 failure diagnosis
  - planner context 包含 recent recovery
  - judge 输出新增字段后仍能被 runtime 正常消费

## 本阶段验收标准

- 至少 80% 的常见失败能落入预定义 taxonomy
- planner context 中可看到最近 2 次失败和恢复动作
- tool/sandbox 层错误不再只有文本 message
- 现有 workflow 行为不发生明显回归

## Milestone 2：能力视角进入规划主链路

## 阶段目标

让系统具备“判断自己缺什么能力”的能力。

这一阶段完成后，系统应做到：

- planner 能看到能力列表而不仅是 node 类型
- runtime 能区分“已有能力未正确使用”和“真正缺失能力”
- 出现能力缺口时，不直接退回通用 replan

## 本阶段范围

本阶段做：

- capability 数据结构
- capability registry
- capability diagnosis node
- planner/judge 对 capability 的消费

本阶段不做：

- 自动生成工具
- 自动修改 registry
- 复杂 capability macro

## 需要新增的数据结构

建议新增文件：

- `agent_runtime_framework/capabilities/models.py`
- `agent_runtime_framework/capabilities/registry.py`

建议定义：

```python
@dataclass(slots=True)
class CapabilitySpec:
    capability_id: str
    description: str
    intents: list[str] = field(default_factory=list)
    toolchains: list[list[str]] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    failure_signatures: list[str] = field(default_factory=list)
    verification_recipe: list[str] = field(default_factory=list)
    extension_policy: str = "reuse_only"
```

## 建议首批能力清单

首批只抽象高频、低争议能力：

- `resolve_target_in_workspace`
- `search_workspace_content`
- `search_workspace_symbols`
- `read_workspace_evidence`
- `move_or_rename_path`
- `edit_workspace_file`
- `run_workspace_verification`
- `inspect_test_failure`

## 需要新增的节点

建议新增文件：

- `agent_runtime_framework/workflow/nodes/capability_diagnosis.py`

新增节点类型：

- `capability_diagnosis`

输入：

- latest tool error
- latest failure diagnosis
- available capabilities
- recent failed actions
- working memory

输出：

- `missing_capability`
- `preferred_capability_ids`
- `recovery_mode`
- `human_handoff_required`

## 需要改造的模块

### 1. `workflow/planning/subgraph_planner.py`

文件：

- [agent_runtime_framework/workflow/planning/subgraph_planner.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/workflow/planning/subgraph_planner.py)

改造目标：

- 让 planner 使用 capability 中层抽象

建议：

- context 中加入 `available_capabilities`
- judge 指出 capability gap 时优先规划 `capability_diagnosis`
- 对高频任务优先由 capability 展开为 nodes

### 2. `workflow/planning/judge.py`

改造目标：

- judge 明确输出 `capability_gap`
- 输出 `preferred_capability_ids`

### 3. `workflow/nodes/registry.py`

改造目标：

- 注册 `capability_diagnosis` executor

### 4. `workflow/context/model_context.py`

改造目标：

- planner/judge context 暴露 capability 视图

建议新增：

- `available_capabilities`
- `ineffective_capabilities`
- `missing_capabilities`

## 本阶段建议实施顺序

1. 新建 `CapabilitySpec` 和 `CapabilityRegistry`
2. 注册首批静态 capability
3. 新增 `capability_diagnosis` 节点和 executor
4. 改造 planner context
5. 改造 judge contract
6. 让 planner 在部分路径使用 capability 展开
7. 补测试

## 本阶段测试清单

- `tests/test_workflow_runtime.py`
  - capability diagnosis 节点可正常执行
  - capability gap 可触发目标恢复分支
- `tests/test_workflow_decomposition.py`
  - planner 在 capability context 存在时输出更稳定
- 新增：
  - `tests/test_capability_registry.py`
  - `tests/test_capability_diagnosis.py`

## 本阶段验收标准

- 系统能识别“工具存在但没用对”和“工具确实不存在”
- 至少 5 个高频任务可映射为 capability
- planner 输出开始体现 capability 中层决策

## Milestone 3：恢复闭环和验证闭环

## 阶段目标

让系统具备“恢复后证明自己修好了”的能力。

这一阶段完成后，系统应做到：

- 写操作后自动进入验证流程
- 环境修复后自动重放失败命令或测试
- 能力恢复结果会回流到 judge 和 memory

## 本阶段范围

本阶段做：

- verification recipe
- capability macro
- 恢复后验证回流

本阶段不做：

- 任意代码生成型新工具

## 需要新增的数据结构

建议新增文件：

- `agent_runtime_framework/workflow/recovery/verification.py`

建议定义：

```python
@dataclass(slots=True)
class VerificationRecipe:
    recipe_id: str
    steps: list[str] = field(default_factory=list)
    required: bool = True
    on_failure_recovery_mode: str = "collect_more_evidence"
```

## 需要新增的能力层抽象

建议新增 capability macro 概念：

- 不是新增原子工具
- 而是把一组已有 toolchain 固化成可复用能力配方

示例：

- `inspect_and_patch_file`
- `repair_and_verify_python_test`
- `resolve_target_then_read`

## 需要改造的模块

### 1. `workflow/nodes/core.py`

文件：

- [agent_runtime_framework/workflow/nodes/core.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/workflow/nodes/core.py)

改造目标：

- 让 verification 节点支持 recipe 化执行结果

### 2. `workflow/nodes/workspace_write.py`

文件：

- [agent_runtime_framework/workflow/nodes/workspace_write.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/workflow/nodes/workspace_write.py)

改造目标：

- 写操作成功后输出验证建议
- 明确是否必须进入 verification recipe

### 3. `workflow/runtime/agent_graph.py`

改造目标：

- 恢复成功后进入验证，再回 judge

### 4. `workflow/context/model_context.py`

改造目标：

- 暴露 `last_verification_result`
- 暴露 `verification_pending`

## 本阶段建议实施顺序

1. 定义 `VerificationRecipe`
2. 为首批 capability 配置 verification recipe
3. 改造 workspace write 节点输出验证信号
4. 改造 verification 节点支持 recipe
5. 改造 agent graph 形成“恢复 -> 验证 -> judge”链路
6. 补 capability macro
7. 补测试

## 本阶段测试清单

- 写文件后必须触发 verification
- verification 失败后应回流正确 recovery mode
- 环境修复后重新跑失败命令
- judge 能读取 last verification result

建议新增：

- `tests/test_workflow_verification_recipes.py`
- `tests/test_capability_macros.py`

## 本阶段验收标准

- 所有 graph-native write node 都有明确验证出口
- 恢复动作结束后不再直接 final answer
- 至少 2 个高频修复场景实现“恢复后自动验证”

## Milestone 4：受控能力扩展

## 阶段目标

只在必要时允许 Agent 受控地新增能力。

这一阶段完成后，系统应做到：

- 只有在 registry 无现成能力时才进入扩展路径
- 新增能力必须被验证、审批和审计
- 能力扩展不会使工具箱失控

## 本阶段范围

本阶段做：

- controlled extension policy
- 受控扩展目录
- registry 接入
- 审批和 smoke verification

## 本阶段前置条件

只有满足以下前提才允许启动本阶段：

- failure taxonomy 稳定
- capability registry 已投入使用
- verification recipe 已经生效
- recovery/approval/observability 已能覆盖扩展路径

## 扩展策略

建议只开放两类扩展：

### 1. Capability Macro Extension

说明：

- 仅组合现有工具
- 不增加新的执行权限

优先级：

- 最高

### 2. Scoped Tool Adapter Extension

说明：

- 仅允许在受控目录生成适配层
- 必须声明 schema、权限和验证方式

优先级：

- 次高

不建议开放：

- 任意 Python 脚本生成并自动执行
- 无审批写入核心 runtime 模块

## 建议新增目录

- `agent_runtime_framework/capabilities/generated/`
- `agent_runtime_framework/workflow/workspace/tools/generated/`

## 需要改造的模块

### 1. `workflow/state/approval.py`

文件：

- [agent_runtime_framework/workflow/state/approval.py](/Users/munan/Documents/munan/my_project/ai/Agent_test/agent-runtime-framework/agent_runtime_framework/workflow/state/approval.py)

改造目标：

- 为能力扩展增加专门 approval kind

### 2. `workflow/runtime/agent_graph.py`

改造目标：

- 支持扩展前审批、扩展后验证、扩展失败回退

### 3. `observability/*`

改造目标：

- 记录扩展来源、用途、验证结果、审批状态

## 本阶段建议实施顺序

1. 定义扩展 policy
2. 实现 capability macro extension
3. 实现 scoped tool adapter extension
4. 加入 approval gate
5. 加入 smoke verification
6. 加入审计日志
7. 补测试

## 本阶段测试清单

- 无现成 capability 时才能进入扩展路径
- 扩展前必须审批
- 扩展后必须验证
- 扩展失败必须回退到 human handoff 或安全恢复

建议新增：

- `tests/test_capability_extension_policy.py`
- `tests/test_capability_extension_approval.py`
- `tests/test_capability_extension_verification.py`

## 本阶段验收标准

- 扩展路径全程可审计
- 无法绕过审批直接写入运行时能力
- 扩展失败不会污染主工作流

## 建议任务拆分方式

为了便于排期，建议每个里程碑再拆成 3 类任务：

### A 类：数据与契约

包括：

- dataclass
- payload
- contract
- persistence

### B 类：运行时接入

包括：

- planner
- judge
- runtime
- node registry
- tool runtime

### C 类：测试与文档

包括：

- 单元测试
- 集成测试
- 文档同步

建议每个里程碑按 `A -> B -> C` 顺序完成，避免边改行为边改契约造成返工。

## 推荐的实际排期

如果按稳妥推进，建议如下：

### 第 1 周

- 完成 Milestone 1 的 A、B 类任务

### 第 2 周

- 完成 Milestone 1 的 C 类任务
- 启动 Milestone 2 的 A 类任务

### 第 3 周

- 完成 Milestone 2 的 B、C 类任务

### 第 4 周

- 完成 Milestone 3 的 A、B 类任务

### 第 5 周

- 完成 Milestone 3 的 C 类任务
- 评估是否具备启动 Milestone 4 的条件

### 第 6 周及以后

- 小范围试点 Milestone 4

## 不建议现在做的事

以下内容建议明确延后：

- 让 agent 直接生成任意 shell tool
- 让 agent 直接修改核心 planner/runtime 代码并立即加载
- 为了“智能感”过早引入大而泛的能力库
- 在 failure taxonomy 尚未稳定前就推进自动扩展

## 最小落地顺序

如果只能先做最小版本，推荐顺序如下：

1. Milestone 1 全做
2. Milestone 2 只做 `CapabilityRegistry + capability_diagnosis`
3. Milestone 3 只做 write-path verification recipe
4. Milestone 4 暂缓

这样可以用最小成本先得到最关键升级：

- Agent 会更清楚自己为什么失败
- Agent 会更清楚自己缺什么
- Agent 在修改后更能证明自己完成了

## 完成定义

当以下条件同时满足时，可认为本计划的主目标已实现：

1. runtime 能稳定给出结构化失败分类
2. planner/judge 能消费 failure 和 capability context
3. 常见任务可通过 capability 中层抽象表达
4. 写操作和修复操作后存在明确 verification path
5. 能力扩展路径必须经过审批、验证和审计

## 总结

这份实施计划的核心不是“把系统变得更复杂”，而是让当前已经较成熟的 graph runtime 逐步获得三种更强的能力：

- 理解失败
- 理解能力缺口
- 理解恢复后的完成证明

推进上必须克制：

- 先做 taxonomy，再做 capability
- 先做 verification，再做 extension
- 先做治理，再做自治

只要按这个顺序推进，系统就会沿着可控方式，从一个 workflow agent 稳定升级为更接近真正 harness 的 reflexive agent system。
