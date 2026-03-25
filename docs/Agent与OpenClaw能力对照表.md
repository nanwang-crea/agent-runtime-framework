# Agent Runtime Framework 与 OpenClaw Agent 能力对照表

## 目的

这份文档用于对比当前 `agent-runtime-framework` 中自研 Agent 与 `openclaw` 中 Agent 在“影响任务理解与执行质量”的关键能力上的差异。

重点不是比较谁的代码更多，而是比较：

- 哪些能力已经具备
- 哪些能力还比较薄
- 哪些差异会直接影响用户体感中的“聪明程度”
- 后续最值得优先补齐的部分是什么

---

## 一句话结论

当前 `agent-runtime-framework` 已经具备一个不错的单 Agent 骨架，尤其在：

- capability 抽象
- plan -> act -> review 闭环
- resource semantics 初步建模
- approval / resume 基础能力

这些方面方向是对的。

但和 `openclaw` 相比，当前系统仍明显缺少一层更厚的“运行时认知工程”，包括：

- 更完整的系统 prompt 结构
- 更强的上下文注入
- 更稳定的任务语义与任务状态机
- 更成熟的工具使用 guidance
- 更强的历史整理与 follow-up 理解
- 更完整的失败恢复与测试覆盖

因此用户会感受到：当前自研 Agent 能执行，但经常“不够全面”“不够稳”“不够像真正理解了任务”。

---

## 总体对照表

| 维度 | agent-runtime-framework | openclaw | 对智力体感的影响 |
| --- | --- | --- | --- |
| 系统提示词 | 已有多个局部 prompt，但更偏短指令、局部职责 prompt | 有统一的、分段式的大系统 prompt，覆盖工具、技能、文档、内存、消息、沙箱、运行时、心跳等 | `openclaw` 更像“知道自己在什么环境里工作”；自研 Agent 更容易只盯住局部动作 |
| 上下文注入 | 主要依赖当前输入、session、plan、resource 解析结果 | 会注入 `AGENTS.md`、`TOOLS.md`、`MEMORY.md` 等 workspace context，并组织 runtime 信息 | `openclaw` 更容易一开始就理解项目规则、工作区约束和协作方式 |
| 任务分类 | 已有 task profile，但 fallback 仍较依赖 marker / 关键词 | 依赖更完整的系统提示、上下文和历史整理，任务切换更稳定 | 自研 Agent 更容易被用户表达变化带偏 |
| planner 机制 | 已在向状态驱动演进，但当前仍保留不少 regex / 模式匹配逻辑 | 虽然也不全靠“自由推理”，但整体运行时为规划提供了更强语义支撑 | 自研 Agent 在复杂自然语言请求上更容易只命中局部动作 |
| resource semantics | 已开始显式区分 file / directory，并传播 allowed actions | 环境上下文、工具说明、workspace 注入更成熟，资源理解更稳 | 自研 Agent 已经有正确方向，但还没完全主导所有 planning 决策 |
| evaluator | 已支持 evidence insufficiency、目录类恢复、合成回答 | 回答质量不只靠 evaluator，而是由整套 prompt / tools / context / history 共同保证 | 自研 Agent 更容易出现“工具结果有了，但回答还像没完全理解” |
| 工具使用指导 | 有 tool schema / capability metadata，但给模型的使用 guidance 还偏薄 | 工具用途、何时调用、长任务等待方式、何时起 subagent 等都有明确说明 | `openclaw` 在“知道下一步该怎么用工具”上更稳 |
| 会话历史组织 | 有 session 和记忆对象，但历史整理能力还偏轻 | 会专门重组 conversation entries，突出当前消息与历史上下文 | `openclaw` 在 follow-up 理解上更强 |
| 记忆机制 | 有 session memory / working memory / index memory 骨架 | 有 memory recall 指导和按需 memory search / memory get 协议 | `openclaw` 更会在历史决策、偏好、长期任务上保持连续性 |
| 子代理 / 多代理 | 当前仍是单代理为主 | 有 subagent、session spawn、跨 session 协作能力 | `openclaw` 面对复杂任务更容易拆解，不必把所有理解压在一个 loop 里 |
| 失败恢复 | 已开始做 retriable、recover_failed_action、目录误读恢复 | 运行时策略更成熟，工具协议和系统提示共同减少失败外露 | 自研 Agent 在失败后更容易显得“卡住”或“答非所问” |
| 测试覆盖 | 已有测试，但更偏框架行为与阶段性能力 | 有大量 prompt、history、agent 行为、回归类测试 | `openclaw` 的“稳定聪明”更多来自长期回归，不只是单次设计 |

---

## 分项细化对照

### 1. 系统提示词层

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| prompt 组织方式 | planner / evaluator / router 各自有 prompt，但偏局部 | 有统一主系统 prompt，按 section 组织 |
| prompt 内容密度 | 主要描述当前模块该做什么 | 除“做什么”外，还描述“在哪做、有什么工具、如何协作、何时读 docs、何时查 memory、何时发消息、何时起 subagent” |
| 对模型的约束方式 | 以单轮 JSON 输出约束为主 | 同时约束身份、工具、协作、环境、消息行为、运行时边界 |

影响：

- 你的 Agent 更像“被交代当前步骤的执行者”
- `openclaw` 更像“知道完整工作制度的工作型代理”

结论：

- 自研 Agent 的 prompt 不是不能用，而是还不够厚
- 后续最重要的不是继续给某个 prompt 补一句规则，而是建立统一 runtime-level system prompt

---

### 2. 上下文注入层

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| 工作区规则注入 | 较少，更多依赖当前 request 和 resolver | 自动注入 bootstrap 文件，如 `AGENTS.md`、`TOOLS.md`、`MEMORY.md` |
| runtime 信息注入 | 有 runtime event / context，但注入给模型的信息较轻 | 会注入 repo、host、OS、model、channel、capabilities、workspace 等 |
| 文档接入 | 主要依赖代码内设计 | prompt 会明确告诉 agent 什么时候读本地 docs |

影响：

- 你的 Agent 更容易在“项目语境”里失明
- `openclaw` 更容易在一开始就知道当前仓库的规范和边界

结论：

- 这类能力非常影响“全面理解任务”的体感
- 用户说一句模糊请求时，真正强的 agent 不是临时猜，而是靠已有上下文去补全语义

---

### 3. 任务理解与任务分类

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| 分类维度 | `chat` / `repository_explainer` / `change_and_verify` | 没有完全依赖显式 profile，但整体运行时把任务理解做得更厚 |
| fallback 机制 | 仍较依赖 marker、关键词和 target hint 抽取 | 有更强系统 prompt、消息历史整理、工具上下文、workspace 注入共同辅助 |
| 表达鲁棒性 | 同一意图换种说法，仍可能落到不同路径 | 对真实表达的包容度更高 |

影响：

- 你现在最大的短板之一就是：任务理解还没完全从“字符串模式”升级到“任务语义”

结论：

- 用户觉得 Agent 笨，最常见原因不是不会执行，而是任务入口就走偏了

---

### 4. Planner 与状态机能力

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| 主循环 | 已有 plan -> act -> review -> continue/stop | 有更成熟的主运行时，且不同环节有更多支撑信息 |
| 高阶任务策略 | 文档中已规划，但还没有完全硬化 | 复杂任务更容易借助 prompt 规则、工具协议、subagent 等形成稳定流程 |
| 自然语言到动作序列映射 | 仍部分依赖 regex | 更少依赖某个单点 regex 规则 |

影响：

- 你的 Agent 已经有 loop，但还没有完全“状态机化”
- 没状态机化之前，模型容易想到哪做到哪

结论：

- “聪明的 Agent”往往不是自由发挥更多，而是高频任务上更稳定

---

### 5. Resource Semantics 与目标理解

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| 资源类型表达 | 已支持 file / directory / allowed_actions | 不一定以相同结构暴露，但整体资源与 workspace 语义更成熟 |
| follow-up 引用 | 已开始支持 last focus / 当前目录 / 下面 / 里面 | 历史组织和上下文协议更完善 |
| planner 使用程度 | 已经接入，但还未完全成为唯一决策依据 | 更像是整体环境理解的一部分 |

影响：

- 这一块其实是你框架里最有潜力的亮点之一
- 一旦让 resource semantics 真正主导规划，任务理解会明显变稳

结论：

- 这部分不是落后方向，而是“还没彻底吃透到全链路”

---

### 6. 工具层与工具使用 guidance

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| 工具注册 | 已有 tool registry、capability registry、MCP 接入 | 同样完整，但对模型呈现的工具说明更成熟 |
| 给模型的工具说明 | 偏 schema 和局部 prompt 提示 | 明确告诉模型每类工具什么时候用、怎么用、不要怎么用 |
| 长任务处理 | 有 loop 和 retry，但整体工作协议较轻 | 有 process、subagent、消息、session 级协作协议 |

影响：

- 不是“有没有工具”决定 agent 聪不聪明，而是“模型是否真的知道怎么串联这些工具”

结论：

- 你的工具层抽象已经不错，但缺 prompt-facing guidance 层

---

### 7. 回答综合能力

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| 原始工具结果处理 | 已开始防止直接把工具输出甩给用户 | 整套 runtime 更倾向于让结果先被组织再回复 |
| synthesize 机制 | 已有，但仍常依赖 evaluator 补位 | synthesize 不只是 evaluator 责任，而是系统整体产物 |
| 证据充分性判断 | 已开始做，但覆盖面还有限 | 更成熟，很多情况下在前置环节就避免了“证据不足直接结束” |

影响：

- 你现在的 Agent 常给人一种“执行到了，但没完全讲透”的感觉

结论：

- 用户体感里的“聪明”，很大一部分来自综合表达，而不是工具调用成功

---

### 8. 历史、记忆与连续性

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| session memory | 已有基础记忆对象 | 有更成熟的 memory recall 协议和历史组织方式 |
| working memory | 已支持 run-scoped memory | 也有，但和整体系统 prompt / memory tool 协同更紧 |
| follow-up 理解 | 正在改善 | 更强，尤其在“刚刚那个”“上一个结果”“之前的决定”这类连续任务上 |

影响：

- 多轮交互时，Agent 是否像“真的记得刚刚在做什么”，对智力体感影响极大

结论：

- 你的 memory 数据结构已经有了，但还需要把它更多转成 prompt 中可消费的上下文

---

### 9. 恢复能力与容错体验

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| 失败处理 | 已有 retriable、recover_failed_action、目录误走恢复 | 整体恢复体验更成熟，失败较少直接暴露为原始异常 |
| 恢复策略来源 | 仍部分由 evaluator / plan expansion 驱动 | 多层协同：prompt guidance、tool usage style、runtime policy |
| 用户体感 | 失败时仍可能显得“卡住” | 更像“换个办法继续做” |

影响：

- 真正聪明的 Agent，失败时往往更显得聪明，因为它会自恢复

结论：

- 你的恢复方向已经很好，但还需要把常见失败模式系统化

---

### 10. 测试与稳定性来源

| 对比项 | agent-runtime-framework | openclaw |
| --- | --- | --- |
| 测试重点 | 框架能力、模块正确性、阶段性行为 | prompt、history、system prompt、路由、回归、会话行为都在测试 |
| 测试语料 | 已开始意识到 benchmark 句式不够 | 更接近真实运行时回归 |
| 稳定性来源 | 架构设计为主 | 架构设计 + 长期回归打磨 |

影响：

- 很多“聪明”其实不是模型突然变聪明，而是团队把容易犯傻的情况都测掉了

结论：

- 后续想提升用户体感，测试集必须从标准表达迁移到真实自然表达

---

## 最影响当前体感的差距排序

下面这些差距，对当前“感觉 Agent 有点笨”的影响最大，按优先级排序：

1. 缺统一而厚的 runtime-level system prompt
2. 任务理解仍未完全摆脱关键词 / regex 驱动
3. 高价值任务还没完全固化成稳定状态机
4. workspace / project context 注入不足
5. 工具使用 guidance 不够强
6. follow-up 历史整理与记忆外显不够
7. 证据充分性判断覆盖面还不够广

---

## 当前自研 Agent 的优势

虽然和 `openclaw` 比还有差距，但当前系统也有自己的优势，不能只看到短板。

### 1. 架构可控性更强

`agent-runtime-framework` 的模块边界很清楚：

- assistant
- agents/codex
- resources
- tools
- memory
- policy
- runtime

这意味着后续你可以更定向地补一层，而不是在一个大系统里到处打补丁。

### 2. Resource Semantics 方向是正确的

很多 Agent 看起来聪明，其实本质上是“目标语义”和“资源语义”做对了。

你现在已经开始做：

- file / directory 区分
- allowed actions
- last focus
- target semantics 传播

这是非常对路的。

### 3. Plan / Recovery 机制已经有了骨架

你不是完全没有 planner 和恢复，而是：

- 已经有 task plan
- 已经有 recover_failed_action
- 已经有 approval / resume
- 已经有 evaluator 驱动的继续探索

所以你离“更聪明”不是重写系统，而是把已有骨架继续加厚。

---

## 后续建议：最值得优先补的能力

### 第一优先级

1. 建立统一主系统 prompt，而不是分散在 router / planner / evaluator 各自补 prompt
2. 把 `repository_explainer`、`file_reader`、`change_and_verify` 三条主链路彻底状态机化
3. 让 resource semantics 成为 planner 的一等输入，而不是补充信息

### 第二优先级

4. 为工具增加面向模型的 guidance 层，而不只是 schema
5. 强化 follow-up 历史打包和最近焦点资源注入
6. 扩大 evaluator 的“证据充分性判断”覆盖面

### 第三优先级

7. 用真实自然语言测试集替换掉一部分 benchmark 式用例
8. 为高频失败路径建立标准恢复模板
9. 后续再考虑更强的子代理或多任务拆解能力

---

## 最终判断

如果把当前差异说得最直白一些：

- `agent-runtime-framework` 当前更像“具备正确抽象的 Agent 框架原型”
- `openclaw` 当前更像“经过真实任务长期打磨的工作型 Agent 运行时”

前者的核心问题不是方向错，而是“让模型显得聪明的隐性支架还不够厚”。

因此，后续提升智力体感的关键，不是只换更强模型，也不是只给 planner 补几条规则，而是把以下几层真正做厚：

- 统一上下文
- 统一任务语义
- 统一资源语义
- 稳定状态机
- 工具 guidance
- 失败恢复
- 历史与记忆外显

只要这几层补起来，当前框架的体感会有非常明显的提升。
