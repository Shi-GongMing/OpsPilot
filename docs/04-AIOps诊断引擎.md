# 04 — AIOps 诊断引擎：Plan-Execute-Replan 全链路

> **面试重点**：Plan-Execute-Replan vs ReAct 区别、StateGraph 手写三节点、三层安全收敛阀
>
> **涉及文件**：`agent/aiops/state.py` → `planner.py` → `executor.py` → `replanner.py` → `services/aiops_service.py`

---

## 0. ReAct vs Plan-Execute-Replan：什么时候用哪个

| 维度 | ReAct | Plan-Execute-Replan |
|------|-------|---------------------|
| 模式 | 边想边做，每步决策一次 | 先全局规划再逐步执行 |
| LLM 调用次数 | N 步任务调 N 次 | 规划时 1-2 次 |
| 成本 | 高 | **最高省 90%** |
| 灵活度 | 高（每步可调整） | 中（需显式 replanning） |
| 适用场景 | 短任务、高度动态 | 长任务、可预规划的复杂任务 |

**我们的选择**：对话用 ReAct（短平快），诊断用 Plan-Execute-Replan（多步结构化推理）。

---

## 1. 状态定义 — StateGraph 的核心

```python
class PlanExecuteState(TypedDict):
    input: str                                          # 用户任务描述
    plan: List[str]                                     # 执行计划（步骤列表）
    past_steps: Annotated[List[tuple], operator.add]    # 已执行步骤（追加式更新）
    response: str                                       # 最终响应/报告
```

**关键设计**：`past_steps` 使用 `Annotated[List[tuple], operator.add]`。这不是普通的 Python list——它是 LangGraph 的 **reducer 机制**。当节点返回 `{"past_steps": [(task, result)]}` 时，LangGraph 不是用新值覆盖旧值，而是**追加到现有列表**。这意味着每个执行过的步骤都自动累积到 `past_steps` 中，无需手动拼接历史。

面试时可以这样讲：**"StateGraph 的 reducer 机制让我们可以用声明式的方式管理增量状态——节点只返回新增内容，框架负责合并。"**

### [自研概念] TypedDict / Annotated / operator.add reducer / StateGraph

---

## 2. 三节点协作流程

```
用户输入故障描述
       │
       ▼
┌──────────┐
│ PLANNER  │  ← 检索向量库中的历史相似案例 + 获取所有可用工具
│ 制定计划  │     LLM 结构化为步骤列表 (structured output → Plan)
└────┬─────┘
     │
     ▼
┌──────────┐
│ EXECUTOR │  ← 取出 plan[0]（当前步骤）
│ 执行步骤  │     LLM + Function Calling → ToolNode 自动执行工具调用循环
└────┬─────┘     返回 (task, result) → 追加到 past_steps, plan 移除第一项
     │
     ▼
┌───────────┐
│ REPLANNER │  ← 基于已执行结果，LLM 决策三种动作之一：
│ 评估+决策  │     respond: 信息够了，生成最终报告
└─────┬─────┘     continue: 计划合理，继续执行
      │            replan: 计划需要调整，替换剩余步骤
      │
  ┌───┴───┐
  │       │
  ▼       ▼
继续执行  生成报告 → SSE 推送 → 触发归档
```

---

## 3. 条件边的路由逻辑

```python
def should_continue(state):
    if state.get("response"):      # 已生成最终报告
        return END
    if state.get("plan"):          # 还有未执行的步骤
        return NODE_EXECUTOR
    # 计划为空但无响应 → 回 replanner 兜底生成
    return NODE_REPLANNER
```

条件边让 LangGraph 在运行时根据状态动态决定下一步。这是 StateGraph 区别于普通 Chain 的核心能力。

---

## 4. 三层安全收敛阀（核心工程亮点）

**面试官最爱问的问题**："Agent 无限循环怎么办？"

我们的回答分三层：

| 层 | 机制 | 触发条件 | 设计理念 |
|:---:|------|---------|---------|
| 1 | 步数硬上限 | 累积 ≥8 步，强制 respond | 绝对安全底线——无论如何 8 步内必须有结论 |
| 2 | 决策软约束 | ≥5 步后禁止 replan，只能 respond | 防止 Agent 反复推翻自己的计划 |
| 3 | 计划膨胀抑制 | replan 新步骤数 ≤ 当前剩余步骤数 | 防止 Agent 通过重规划不断扩充任务 |

**面试时的一句话**：

> "我们设计了三层安全阀——步数硬上限是绝对防线（8 步强制结束），决策软约束防止 Agent 反复推翻自己（5 步后禁止 replan），膨胀抑制让 replan 不能比原计划更长。核心原则是'够好就结束，不追求完美但停不下来'。"

这不是简单的 `max_iterations` 参数——是一个**分层递进的收敛策略**。面试官听到三层有梯度的设计，比听到"我设了上限"加分多得多。

### [自研概念] structured output (LangChain `with_structured_output`)

---

## 5. Planner 的设计细节

Planner 制定计划前先做两件事：

**① 从向量库检索历史经验**：调用 `retrieve_knowledge` 工具，把当前故障描述作为查询，找相似的历史案例。找到的经验作为 `experience_context` 注入 Planner 的 prompt 中。

**② 获取全量工具列表**：本地 3 个 + MCP 7 个，格式化后让 LLM 知道"有哪些工具可用"。

然后 LLM 用 `with_structured_output(Plan)` 生成结构化的步骤列表。`Plan` 是一个 Pydantic 模型：

```python
class Plan(BaseModel):
    steps: List[str] = Field(description="按顺序执行的步骤列表")
```

**为什么要 structured output**：如果让 LLM 自由文本输出计划，后续 Executor 需要解析文本——容易出错。用 Pydantic 约束输出格式，LLM 必须按 schema 返回，代码直接拿到 `steps` 列表。

---

## 6. Executor 的设计细节

Executor 只执行当前这一步，不关心全局：

- 取出 `plan[0]`，构造 prompt："请执行以下任务：{task}"
- LLM `bind_tools(all_tools)` 绑定工具 → LLM 自主决定是否调工具
- 如果 LLM 返回 `tool_calls`，ToolNode 自动执行工具，结果返回给 LLM 二次推理
- 最终返回 `(task, result)`，由 reducer 自动追加到 `past_steps`

**为什么 Executor 不返回整个对话历史而只返回当前步骤结果**：Executor 的执行历史通过 `past_steps`（reducer 追加）和 `plan`（返回时弹出第一项）两个字段管理。这比在 Executor 内部手写历史管理要简洁得多——LangGraph 的 channel 机制替你做了状态合并。

---

## 7. Replanner 的设计细节

Replanner 是决策中心。它基于 `past_steps`（已执行了什么、结果如何）和 `plan`（还剩什么）来决定下一步。

**为什么需要 Replanner 而不用简单的 continue/end 判断**：因为计划可能不合理——第一步执行后发现方向错了，需要重新规划。Replanner 不是简单的"检查是否还有剩余步骤"，而是**基于执行结果重新评估计划的合理性**。

三种决策的优先级是 `respond > continue > replan`（在 prompt 中明确约束）。

---

## 8. 涉及的文件与职责

| 文件 | 职责 |
|------|------|
| `agent/aiops/state.py` | `PlanExecuteState` 状态定义（TypedDict + reducer） |
| `agent/aiops/planner.py` | 检索经验 → 获取工具 → LLM 生成 Plan |
| `agent/aiops/executor.py` | 取出当前步骤 → Function Calling → ToolNode → 返回结果 |
| `agent/aiops/replanner.py` | 评估已执行结果 → 决策 continue/replan/respond → 或生成最终报告 |
| `agent/aiops/utils.py` | 工具列表格式化为描述文本 |
| `services/aiops_service.py` | StateGraph 构建、条件边、流式 SSE 推送、归档触发 |

---

## 面试话术

> "诊断用 Plan-Execute-Replan——Planner 先检索历史案例再制定计划，Executor 按步骤执行并自动调工具，Replanner 评估结果决定继续、重规划还是生成报告。
>
> 最核心的设计是三层安全收敛——步数硬上限 8 步是绝对防线，5 步后禁止 replan 防止反复推翻自己，膨胀抑制让新步骤数不能超过剩余步骤数。核心原则是'够好就结束'。实现上我们手写了 LangGraph StateGraph，用 TypedDict 定义状态，用 operator.add reducer 让 past_steps 自动累积，用 structured output 约束 LLM 的输出格式。"
