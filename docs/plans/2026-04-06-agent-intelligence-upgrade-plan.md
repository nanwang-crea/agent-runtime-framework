# Agent 智能性升级实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将当前 Agent 从“能执行流程”升级为“能理解进展、诊断问题、调整策略并解释原因”的工作流智能体。

**Architecture:** 本次升级不引入硬编码的 fallback 规则树，而是通过增强状态表达、Judge 反思能力、Planner 重规划输入和恢复策略层，让系统在图运行过程中具备更自然的自我纠偏能力。核心思路是把“判断为什么不够好”和“决定下一步如何改变”做成一等能力，而不是仅依赖节点成功与否。

**Tech Stack:** Python 3.12、pytest、现有 workflow runtime、模型驱动 planner/judge、结构化 workflow state。

## 一、背景与问题定义

当前 Agent 已经具备基本的图运行能力，但在复杂任务下仍容易表现出“笨”的感觉，主要体现为：

1. 只能按图执行，缺少对当前进展质量的判断。
2. 会继续向前跑，但不清楚自己是否已经偏题。
3. 失败后更像“重试一遍”，而不是“理解为什么失败后再改计划”。
4. 聚合状态过于粗糙，无法支撑高质量反思。
5. Judge 更像规则闸门，不像真正的评审器。
6. Planner 缺少足够的失败上下文，因此重规划质量有限。

本计划的目标不是增加更多工具，而是增强 Agent 的认知闭环：

1. 看清自己已经做了什么。
2. 判断当前结果为什么还不够。
3. 决定下一步应该改变什么。
4. 在失败时给出可解释的恢复动作。

## 二、改造原则

### 1. 不引入硬编码 fallback 规则树

本次改造明确避免如下方向：

1. “某异常出现就固定跳某节点”的静态规则树。
2. “先吞错误再继续跑”的隐式兜底逻辑。
3. 把格式问题、语义问题、策略问题全部交给同一种处理机制。

### 2. 顶层负责调度恢复，局部负责修复上下文

恢复能力应当分层：

1. 节点边界负责本地校验、归一化和结构修复。
2. 图运行时负责错误诊断、恢复方式选择和计划调整。
3. 顶层 runtime 负责决定是继续重规划、请求澄清、局部修复还是终止。

### 3. 优先增强判断质量，而不是增加执行花样

如果 Judge 和 Planner 看不清现状，新增节点只会让系统更复杂，不会真正更聪明。因此优先级应为：

1. 状态表达
2. Judge
3. Planner
4. 恢复层
5. 新节点类型

## 三、改造清单

### P0：优先级最高，直接决定 Agent 是否“像在思考”

#### 1. 升级 AgentGraphState 与运行轨迹表达

**目标：** 让系统保留足够的过程信息，支持高质量判断与重规划。

**涉及文件：**
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Test: `tests/test_workflow_runtime.py`

**需要新增的状态：**
- 每轮计划意图
- 每轮执行摘要
- 每轮失败分类
- 已尝试策略列表
- 当前未解决问题列表
- 节点结果质量标记
- 节点失败是否可恢复

**预期结果：**
- Agent 不再只记住“有没有 evidence”
- Agent 能回顾“为什么上一轮不够好”

#### 2. 升级 Judge，为每轮输出结构化评审结论

**目标：** 让 Judge 不再只按数量判断，而是按目标覆盖度、偏题风险、验证完整性来判断。

**涉及文件：**
- Modify: `agent_runtime_framework/workflow/judge.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_decomposition.py`

**Judge 需要回答的问题：**
- 当前结果是否已经真正回答了用户目标
- 当前证据是否只是中间线索
- 是否已经出现偏题
- 是否存在证据冲突
- 是否缺少关键验证
- 下一轮应该改变什么策略

**预期结果：**
- Judge 输出从“继续/停止”升级为“为什么继续，以及下一轮应该如何变化”

#### 3. 升级 Planner 输入与提示词

**目标：** 让 Planner 基于真实上下文重规划，而不是重复上一轮动作。

**涉及文件：**
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/workflow/planner_prompts.py`
- Test: `tests/test_workflow_decomposition.py`

**需要新增的 Planner 上下文：**
- 最近一次失败分类
- 最近一次无效节点
- 已尝试且无效的路径
- 当前关键缺口
- 候选恢复方向
- 未解决问题摘要

**预期结果：**
- 下一轮子图能体现“调整策略”
- 减少重复节点和无效重试

### P1：增强恢复能力，让系统遇错后表现得更成熟

#### 4. 引入恢复策略层

**目标：** 把“发生错误后该怎么办”从隐式异常分支升级为显式恢复流程。

**涉及文件：**
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_runtime.py`

**恢复策略层的职责：**
- 识别当前失败属于哪一类
- 决定下一步是重规划、局部修复、澄清还是终止
- 把恢复决策写入状态，供后续 Planner/Judge 使用

**建议的恢复类别：**
- 结构错误
- 执行错误
- 目标理解错误
- 证据不足
- 验证不足
- 状态不一致

**预期结果：**
- 系统失败后不再只是抛异常或盲目继续
- 恢复动作具备可解释性

#### 5. 给节点结果增加质量信号

**目标：** 区分“节点成功执行”和“节点真的产生有效进展”。

**涉及文件：**
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/filesystem_node_executors.py`
- Modify: `agent_runtime_framework/workflow/tool_call_executor.py`
- Modify: `agent_runtime_framework/workflow/chunked_file_read_executor.py`
- Modify: `agent_runtime_framework/workflow/content_search_executor.py`
- Test: `tests/test_workflow_runtime.py`

**建议增加的质量信号：**
- relevance
- confidence
- progress_contribution
- verification_needed
- recoverable_error

**预期结果：**
- 聚合层能区分“有输出”和“有价值输出”
- Judge 更容易识别偏题与空转

#### 6. 优化聚合层，保留关键推理痕迹

**目标：** 不把执行历史压缩成过于贫乏的 summary。

**涉及文件：**
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Test: `tests/test_workflow_runtime.py`

**需要保留的内容：**
- 关键证据来源
- 重要推断
- 验证结论
- 冲突点
- 未解问题
- 被放弃的尝试及原因

**预期结果：**
- 后续轮次能基于真实历史做判断
- 降低重复工作

### P2：扩展认知节点，让图更像“会思考”的系统

#### 7. 新增高价值认知节点类型

**目标：** 扩展图中的“认知动作”而不是单纯扩展工具调用。

**建议新增节点：**
- `diagnose_failure`
- `repair_plan`
- `compare_evidence`
- `resolve_conflict`
- `decide_next_best_action`

**涉及文件：**
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Create: `agent_runtime_framework/workflow/diagnose_failure_executor.py`
- Create: `agent_runtime_framework/workflow/repair_plan_executor.py`
- Test: `tests/test_workflow_runtime.py`

**预期结果：**
- 图中不仅有“执行动作”，还有“分析动作”
- Agent 更容易表现出策略性而不是机械性

#### 8. 增加智能性评测集

**目标：** 用具体案例检验 Agent 是否真的更聪明，而不是更复杂。

**涉及文件：**
- Create: `tests/test_workflow_agent_intelligence.py`

**建议覆盖场景：**
- 模糊目标解析
- 多步检索任务
- 写后验证任务
- 中途失败后的恢复
- 信息冲突时的决策
- 重复无效动作抑制

**评估指标：**
- 重复节点比例
- 一次完成率
- 失败解释清晰度
- 计划变化合理性
- 验证覆盖率

## 四、分阶段实施计划

### 阶段一：状态与评审基础设施

**目标：** 先让系统知道自己做了什么、哪里不够、为什么不够。

**包含内容：**
1. 扩展 `AgentGraphState`
2. 扩展 `aggregated_payload`
3. 增强 `execution_summary`
4. 升级 `judge_progress`

**完成标准：**
1. 每轮都有结构化评审结果
2. 每轮都有失败分类和缺口说明
3. Judge 输出可以直接供 Planner 使用

### 阶段二：重规划能力升级

**目标：** 让 Planner 真正利用历史上下文生成下一轮计划。

**包含内容：**
1. 扩展 planner prompt
2. 加入失败轨迹输入
3. 加入策略变化建议输入
4. 增加防重复规划测试

**完成标准：**
1. 下一轮计划能体现策略变化
2. 对明显无效的节点不再机械重复

### 阶段三：恢复策略层

**目标：** 让系统面对异常时先诊断，再恢复。

**包含内容：**
1. 在 runtime 中加入失败诊断结构
2. 引入恢复决策状态
3. 区分可恢复错误与不可恢复错误
4. 打通 Planner、Judge 与恢复层的数据流

**完成标准：**
1. 失败后有结构化恢复决策
2. 恢复动作可追踪、可解释

### 阶段四：认知节点扩展

**目标：** 让图不仅会执行，还会比较、诊断和修正。

**包含内容：**
1. 新增 `diagnose_failure`
2. 新增 `repair_plan`
3. 新增 `compare_evidence`
4. 将新节点接入 planner 的可选动作空间

**完成标准：**
1. 复杂失败路径下出现显式诊断与修复动作
2. 图结构能够体现认知过程

### 阶段五：评测与收敛

**目标：** 用测试和案例确保升级带来的是真实收益。

**包含内容：**
1. 建立智能性评测集
2. 量化重复动作与恢复质量
3. 根据测试结果收敛 prompt 和状态结构

**完成标准：**
1. 回归测试稳定
2. 重复动作下降
3. 恢复质量和解释质量提升

## 五、建议的实施顺序

### 第一批立即开始

1. `models.py` 中补充 AgentGraphState 扩展字段
2. `agent_graph_runtime.py` 中补充每轮结构化摘要
3. `judge.py` 中改造 Judge 输出结构
4. 为上述能力补测试

### 第二批紧接着做

1. `subgraph_planner.py` 中扩大 Planner 输入
2. `planner_prompts.py` 中改造提示词
3. 增加“避免重复无效动作”的测试

### 第三批逐步推进

1. 恢复策略层
2. 新认知节点
3. 评测集

## 六、风险与控制点

### 风险 1：状态过度膨胀

**问题：** 如果状态字段加太多，反而会让 Planner 输入噪声变大。

**控制方式：**
1. 保留高价值结构化字段
2. 不把全部日志原样塞给模型
3. 摘要与原始结果分层存储

### 风险 2：Judge 过于复杂导致不稳定

**问题：** 如果 Judge 一次承担过多推理任务，输出可能漂移。

**控制方式：**
1. Judge 输出格式固定
2. 先从少量核心判断项开始
3. 用测试锁定关键行为

### 风险 3：Planner 获得更多上下文后仍然重复

**问题：** 单靠更多输入，不一定自动带来更好规划。

**控制方式：**
1. 强化 prompt 中的“必须体现策略变化”
2. 用测试约束重复无效动作
3. 通过 Judge 提供更明确的缺口描述

## 七、验收标准

满足以下条件，可认为本轮智能性升级达标：

1. Agent 能解释上一轮为什么不足。
2. Agent 能在下一轮明显改变策略，而不是简单重复。
3. Agent 能区分“节点成功执行”和“任务实际推进”。
4. Agent 能在失败后给出结构化恢复动作。
5. 复杂任务下重复节点数量明显下降。
6. 写后验证、模糊目标、多步检索场景的完成质量提升。

## 八、执行建议

建议按如下方式推进：

1. 先完成 P0，不急着新增节点。
2. 等 Judge 和 Planner 的上下文足够清晰后，再做恢复层。
3. 恢复层稳定后，再引入新的认知节点类型。
4. 每一阶段完成后补针对性测试，不用等到最后一起验证。

这样能最大限度避免系统越改越复杂，但智能性没有同步提升的问题。
