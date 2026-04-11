# Reflexive Capability Harness 升级方案

## 背景

当前仓库已经具备一个较完整的 graph-first Agent Runtime：

- workspace 请求统一进入 `RootGraphRuntime -> AgentGraphRuntime -> GraphExecutionRuntime`
- 系统已经具备 planner / judge / approval / resume / persistence / tool execution / memory / basic observability
- 文件系统与文本修改路径已经 graph-native 化

相关实现主要位于：

- `agent_runtime_framework/workflow/runtime/agent_graph.py`
- `agent_runtime_framework/workflow/runtime/execution.py`
- `agent_runtime_framework/workflow/planning/subgraph_planner.py`
- `agent_runtime_framework/workflow/planning/judge.py`
- `agent_runtime_framework/tools/executor.py`
- `agent_runtime_framework/workflow/workspace/tools/*`
- `agent_runtime_framework/memory/manager.py`

这说明当前系统的主要问题已经不再是“有没有 workflow runtime”，而是“runtime 遇到问题后是否知道自己缺少什么能力，并能沿着正确恢复路径继续执行”。

一个更强的 harness，不应只会：

- 规划
- 执行
- 报错
- 重试

还应会：

- 判断失败属于哪一类问题
- 识别当前缺的是信息、能力、参数、环境还是验证
- 优先复用已有工具，而不是盲目新增工具
- 在确实缺失能力时，进入受控的能力补全路径
- 将失败模式和恢复效果沉淀到下一轮 planning context 中

本文档定义下一步 Agent 升级方向：从“流程智能”升级到“能力智能”。

## 问题定义

当前系统已经有 `repair_history`、`recovery_history`、`failure_history`、`quality_signals` 等基础结构，但它们主要承担记录和展示作用，尚未形成能力反思闭环。

当前缺口集中在以下几个方面：

### 1. 失败语义不够明确

当前执行失败大多会落到：

- tool execution failed
- workflow execution failed
- judge replan

但 runtime 缺少统一的失败分类体系，无法稳定区分：

- 目标不明确
- 证据不充分
- 工具不存在
- 工具存在但选错了
- 参数不合法
- shell 被 sandbox 拒绝
- 环境缺依赖
- 修改完成但验证失败

### 2. 规划器尚未真正使用“能力视角”

当前 planner 直接产出 node graph，已经很强，但更偏“动作规划”，还不是“能力规划”。

例如：

- “搜索文件”
- “定位符号”
- “移动路径”
- “修复测试失败”

这些更接近复合能力，而不是单个 node type 或单个 tool name。

### 3. 还没有显式的能力缺口诊断节点

当前已有结构化输出修复逻辑，主要用于修复 planner/judge 的 JSON 契约输出，但没有一个专门节点负责：

- 解释这次失败为什么发生
- 判断当前缺的是哪种能力
- 输出下一步应采取的恢复模式

### 4. 工具扩展缺少受控路径

当前已经有：

- `ToolRegistry`
- `SkillRuntime`
- `McpRegistry`

但缺少一个更上层的能力装配层，也缺少一个“何时允许新增能力、何时必须停止并请求人工确认”的治理机制。

## 目标

本次升级的目标是引入一个 `Reflexive Capability Harness`，让 Agent 在执行中具备显式的能力反思、失败归因和恢复分流能力。

系统应具备以下能力：

1. 对执行失败进行稳定分类，而不是统一视为 replan。
2. 在 planning / judge 之间引入轻量的能力诊断步骤。
3. 让 planner 不只看到 node graph，还能看到近期失败模式、无效策略和可用能力。
4. 优先复用已有工具和 skill，而不是默认新增工具。
5. 在确实缺失能力时，进入受控的能力补全路径。
6. 将恢复决策、恢复效果和验证结果沉淀为可复用状态。

## 非目标

本阶段不追求：

- 让 Agent 无限制地生成任意 Python 工具
- 引入开放式自修改 runtime 内核
- 在没有治理和验证的情况下自动扩张工具箱
- 用更多 orchestrator 层替代当前已较清晰的 graph runtime

## 设计原则

### 1. 工具不是越多越好

优先顺序应为：

1. 选择已有工具
2. 组合已有工具
3. 新增受控能力

### 2. 失败先分类，再恢复

恢复策略必须依赖失败类型，而不是统一重试。

### 3. 能力优先于节点

节点仍然是执行表达单位，但 planner 应逐步提升到以 capability 为中间抽象。

### 4. 恢复必须可观察

每次恢复尝试都应记录：

- 为什么恢复
- 采用了什么恢复模式
- 是否成功
- 为什么成功或失败

### 5. 自动扩展必须受控

任何新增能力都必须经过：

- 明确声明
- 限权执行
- 可验证
- 可追踪

## 当前实现与目标架构映射

### 已有能力

当前系统已经具备以下升级基础：

- `AgentGraphRuntime` 提供循环式子图追加和恢复入口
- `GraphExecutionRuntime` 提供节点执行、审批和 resume 语义
- `subgraph_planner` 已可读取失败历史、执行摘要和 judge 反馈
- `judge` 已可输出 `diagnosis`、`strategy_guidance`、`allowed_next_node_types`
- `MemoryManager` 已可维护 session/working/long-term memory
- `ToolRegistry`、`SkillRuntime`、`McpRegistry` 已经存在
- `quality_signals`、`repair_history`、`recovery_history` 已有基础数据面

### 核心缺口

还缺：

- failure taxonomy
- capability registry
- capability diagnosis node
- recovery mode contract
- verification recipe
- controlled capability extension path

## 目标架构

目标架构如下：

```text
User Goal
  -> Goal Intake
  -> Route Decision
  -> Planner
  -> Dynamic Subgraph
  -> Execution
  -> Judge
       -> Accept
       -> Clarification
       -> Replan
       -> Capability Diagnosis
              -> Use Existing Capability
              -> Compose Existing Capabilities
              -> Controlled Capability Extension
              -> Human Handoff
```

其核心不再是“失败后继续 plan”，而是：

```text
Failure Signal
  -> Failure Taxonomy
  -> Capability Reflection
  -> Recovery Mode Selection
  -> Verification
  -> Memory Update
  -> Re-enter Planning
```

## 新增核心概念

### 1. Failure Taxonomy

建议新增统一失败分类：

- `context_gap`
  - 目标不清
  - 路径不清
  - 需要澄清
- `evidence_gap`
  - 证据不充分
  - 未读到关键文件
  - 结论未被 grounding
- `capability_gap`
  - 缺少某种工具或能力
  - 当前 registry 无法满足任务
- `tool_selection_gap`
  - 工具存在但选错
  - 使用了不适合当前问题的工具链
- `argument_gap`
  - tool schema 满足但参数错误
- `sandbox_gap`
  - 命令被 sandbox 拒绝
  - 路径越权
  - shell meta 被拒绝
- `environment_gap`
  - 缺依赖
  - 测试环境异常
  - 执行环境不完整
- `quality_gap`
  - 修改完成但未验证
  - 验证失败
  - 输出与目标不一致

建议新增结构：

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

### 2. Recovery Mode

建议把恢复方式标准化，而不是只记录自由文本：

- `retry_same_action`
- `repair_arguments`
- `switch_tool`
- `collect_more_evidence`
- `request_clarification`
- `run_verification`
- `repair_environment`
- `compose_capability`
- `extend_capability`
- `handoff_to_human`

### 3. Capability Registry

建议在 `ToolRegistry` / `SkillRuntime` / `McpRegistry` 之上增加一层 `CapabilityRegistry`。

其作用不是替代这些 registry，而是定义“用户可感知能力”。

示例结构：

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

建议优先抽象如下能力：

- `resolve_target_in_workspace`
- `search_workspace_content`
- `search_workspace_symbols`
- `move_or_rename_path`
- `edit_workspace_file`
- `run_workspace_verification`
- `inspect_test_failure`
- `repair_failing_code_path`

### 4. Capability Diagnosis Node

建议新增节点类型：

- `capability_diagnosis`

它的输入应包括：

- latest error
- latest tool metadata
- failure history
- ineffective actions
- task snapshot
- available capabilities

它的输出应包括：

- failure diagnosis
- recovery mode
- missing capability
- preferred capability
- whether human approval is required

### 5. Verification Recipe

当前已有 `verification` 节点，但建议从“单节点”提升为“配方式验证”。

每个能力可以声明自己的验证策略：

- `read-only` 能力通常不需要验证
- `edit` 能力需验证 diff、语法、测试或目标行为
- `environment repair` 能力需重新运行失败命令验证恢复是否生效

示例：

```python
@dataclass(slots=True)
class VerificationRecipe:
    recipe_id: str
    steps: list[str] = field(default_factory=list)
    required: bool = True
    on_failure_recovery_mode: str = "collect_more_evidence"
```

## 运行时行为设计

### 正常主链路

保持当前 graph-first 主链路不变：

```text
goal_intake
  -> plan_n
  -> dynamic_subgraph_n
  -> aggregate_results_n
  -> evidence_synthesis_n
  -> judge_n
```

### 新的失败恢复链路

当执行失败或 judge 判断存在显著缺口时，进入：

```text
failure signal
  -> capability_diagnosis
  -> recovery mode selection
  -> targeted subgraph append
  -> verification
  -> judge
```

### 恢复分流规则

建议的运行规则：

1. 如果是 `context_gap`，优先进入 `clarification` 或 `target_resolution`。
2. 如果是 `evidence_gap`，优先进入 `workspace_discovery / content_search / chunked_file_read`。
3. 如果是 `tool_selection_gap`，优先切换到现有 capability，不新增工具。
4. 如果是 `argument_gap`，优先修复 tool arguments。
5. 如果是 `sandbox_gap` 或 `environment_gap`，优先进入环境修复或请求审批。
6. 如果是 `quality_gap`，必须进入 verification recipe。
7. 只有在 `capability_gap` 且 registry 中无可用替代时，才允许进入 `extend_capability`。

## 规划器上下文升级

建议扩展 planner 上下文，新增以下字段：

- `available_capabilities`
- `recent_failure_diagnoses`
- `recent_recovery_modes`
- `ineffective_capabilities`
- `missing_capabilities`
- `last_verification_result`

这样 planner 在下一轮规划时看到的不只是：

- goal
- working_memory
- judge_decision

还会看到：

- 最近哪类恢复方式无效
- 当前系统已有哪些能力
- 现在缺的到底是能力还是信息

## Judge 合约升级

建议在 judge 输出中增加：

- `recommended_recovery_mode`
- `capability_gap`
- `preferred_capability_ids`
- `verification_required`
- `human_handoff_required`

使 judge 不只是“accept / replan”，还能为恢复路径提供结构化约束。

## Tool Runtime 升级

建议增强 `agent_runtime_framework/tools/executor.py` 的错误元信息。

当前已返回：

- validation error
- execution error
- attempt_count

建议补充：

- `failure_category`
- `failure_subcategory`
- `suggested_recovery_mode`
- `suggested_tools`
- `suggested_capabilities`
- `sandbox_denial_reason`

同时建议让 sandbox 层输出更细的拒绝原因，例如：

- `shell_meta_denied`
- `command_not_allowed`
- `network_blocked`
- `path_outside_workspace`
- `read_only_violation`

## Controlled Capability Extension

自动补全能力建议分三级：

### Level 1: Capability Selection

目标：

- 系统已有工具和 skill 足够，只是当前没选对

动作：

- 切换现有 tool
- 切换现有 capability
- 重新规划 toolchain

### Level 2: Capability Composition

目标：

- 无需新增原子工具，但需要把多个现有能力组合成新工作流

动作：

- 定义 capability macro
- 固化常见恢复配方

示例：

- `grep_workspace + read_workspace_excerpt + apply_text_patch + run_tests`

### Level 3: Controlled Capability Extension

目标：

- registry 中确实不存在当前所需能力

动作：

- 在受限目录下生成能力定义或工具适配层
- 必须声明 schema、权限等级、验证配方
- 必须通过 smoke verification
- 必须记录来源、用途和生效范围

本阶段不建议直接开放任意代码生成型工具扩展。

## 人类接管策略

建议明确以下场景必须触发人类接管或审批：

- destructive write
- 权限提升
- 跨 workspace 路径操作
- 需要外网
- 将新增能力写入 runtime registry
- 恢复链路已连续失败超过阈值

这部分应与当前 approval 模型对齐，而不是并行造新流程。

## 对现有模块的改造建议

### 第一批改造

- `agent_runtime_framework/workflow/runtime/agent_graph.py`
  - 引入 `FailureDiagnosis`
  - 把 `execution_failed -> diagnose_and_replan` 细化为结构化恢复模式
- `agent_runtime_framework/workflow/planning/judge.py`
  - 扩展 judge contract，加入 recovery mode 和 capability gap
- `agent_runtime_framework/workflow/planning/subgraph_planner.py`
  - 让 planner 显式消费 capability 上下文
- `agent_runtime_framework/tools/executor.py`
  - 丰富 tool error metadata
- `agent_runtime_framework/sandbox/core.py`
  - 输出细粒度 sandbox denial reason
- `agent_runtime_framework/workflow/context/model_context.py`
  - 增加 capability/recovery 相关上下文字段

### 第二批新增

- `agent_runtime_framework/capabilities/models.py`
- `agent_runtime_framework/capabilities/registry.py`
- `agent_runtime_framework/workflow/nodes/capability_diagnosis.py`
- `agent_runtime_framework/workflow/recovery/models.py`
- `agent_runtime_framework/workflow/recovery/classifier.py`

### 第三批扩展

- capability macro registry
- verification recipe registry
- controlled capability extension pipeline

## 分阶段实施计划

### Phase 1: Failure Taxonomy + Recovery Contract

目标：

- 先让系统知道“自己为什么失败”

交付：

- 统一失败分类
- recovery mode contract
- tool/sandbox 结构化错误标签
- planner/judge 上下文补充失败与恢复信息

验收标准：

- 常见失败可以被稳定归类
- planner 能看到最近失败模式
- judge 能输出明确恢复建议

### Phase 2: Capability Registry + Capability Diagnosis Node

目标：

- 让系统知道“自己缺什么能力”

交付：

- capability registry
- capability_diagnosis 节点
- planner 基于 capability 而不是纯 node type 进行中层决策

验收标准：

- 系统能够区分“已有能力未正确使用”和“真正缺失能力”
- 常见任务可被映射为稳定 capability

### Phase 3: Verification Recipe + Capability Composition

目标：

- 让系统知道“恢复后如何证明自己真的修好了”

交付：

- verification recipe
- capability macro
- 修改后自动进入适配验证链路

验收标准：

- 写操作和环境修复有明确验证收尾
- 失败恢复不再只停留在“再试一次”

### Phase 4: Controlled Capability Extension

目标：

- 在治理前提下允许有限自扩展

交付：

- 受控能力扩展目录
- 扩展前后验证与审批机制
- 扩展行为 observability

验收标准：

- 新增能力可审计、可回滚、可验证
- 不出现工具箱无序膨胀

## 测试策略

建议新增测试覆盖：

- failure taxonomy 分类测试
- tool runtime 错误标签测试
- sandbox denial reason 测试
- capability diagnosis contract 测试
- planner 在 recent failures 存在时的行为测试
- verification recipe 触发与失败回流测试
- controlled capability extension gating 测试

## 关键风险

### 1. 过早引入自动生成工具

风险：

- 工具箱膨胀
- planner 选择困难
- 行为不可解释

应对：

- 先做 capability selection 和 composition
- 最后再做 extension

### 2. 恢复链路过长

风险：

- runtime 变慢
- 状态复杂

应对：

- 反思节点保持轻量
- 使用结构化 contract，避免无限自由推理

### 3. 失败分类不稳定

风险：

- 恢复模式频繁抖动

应对：

- 优先基于规则和 metadata 做一层分类
- 模型诊断只在高层补充解释

## 建议的最小可执行版本

如果只做一个 MVP，建议范围如下：

1. 引入 `FailureDiagnosis`
2. 定义 `RecoveryMode`
3. 给 tool 和 sandbox 错误打结构化标签
4. 扩展 planner/judge 上下文中的失败与恢复信息
5. 新增 `capability_diagnosis` 节点
6. 在写操作后引入 recipe 化 verification 入口

这个版本做完后，系统就会从：

- 会规划
- 会执行
- 会失败
- 会重试

升级为：

- 会判断自己为什么失败
- 会判断自己缺什么
- 会选更合适的恢复路径
- 会在恢复后验证结果

## 总结

当前仓库已经具备成为强 harness 的绝大部分基础设施。下一步不应继续增加总控层，而应把已有的 planner、judge、tool runtime、memory、observability 贯穿起来，形成“失败归因 -> 能力反思 -> 恢复分流 -> 验证收尾”的闭环。

这次升级的核心不是“增加更多工具”，而是让 Agent 具备以下能力：

- 知道自己为什么卡住
- 知道自己缺的是信息还是能力
- 优先复用已有能力
- 必要时进入受控扩展
- 恢复后证明自己真的完成了任务

这将把当前 runtime 从一个已经不错的 workflow agent，推进为一个更接近真正 harness 的 reflexive agent system。
