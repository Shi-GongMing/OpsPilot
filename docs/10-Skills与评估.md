# 10 — Skills 与评估 Harness

> **面试重点**：Skills 的"零代码扩展"设计理念、评估 Harness 的三维度量化
>
> **涉及文件**：`.claude/skills/*.md`、`services/evaluation_harness.py`

---

## 一、Skills：面向运维的领域快捷指令

### 1. 设计理念

Skills 是声明式指令——每个 Skill 是一个 Markdown 文件，描述"做什么"和"怎么调 API"，不写新代码。底层完全复用 OpsPilot 已有的 Agent、知识库和会话管理基础设施。

**为什么强调"零代码"**：新增运维能力不需要改 Python 代码——运维工程师自己就能写一个新的 Skill 文件。降低了功能扩展的门槛。

### 2. 四个 Skill

| Skill | 命令 | 调用的后端能力 |
|------|------|---------------|
| 诊断 | `/ops-diagnose` | AIOps Plan-Execute-Replan，触发完整诊断并自动归档 |
| 告警 | `/ops-alerts` | ReAct Agent → `query_prometheus_alerts` 工具 → 结果分类 |
| 知识库 | `/ops-knowledge` | RAG 检索、文档上传/向量化、归档统计 |
| 会话 | `/ops-sessions` | AsyncSqliteSaver 持久化会话列表/查看/清理 |

### 3. 面试一句话

> "为运维工程师开发了 4 个声明式 Skills——零额外代码，底层复用已有的 Agent 和知识库。新增运维能力不用改核心代码，写一个 Skill 文件就行。"

---

## 二、评估 Harness：Agent 诊断能力量化

### 1. 为什么需要评估

Agent 诊断的输出是自然语言——不像分类任务有明确的 accuracy。你怎么回答"这个 Agent 诊断准不准？"——需要一套可量化的评估框架。

2025-2026 年行业共识：**Agent = 模型 + 框架，评估应覆盖整个执行栈，不能只评估模型。**

### [自研概念] Agent Evaluation Harness

---

### 2. 评估架构

```
BUILTIN_SCENARIOS (5 个故障场景)
  → AIOpsService.execute(场景描述)
  → Planner → Executor → Replanner → 诊断报告
  → 评审 LLM (qwen3.7-plus, temperature=0)
      ├── root_cause_accuracy   (0-100)
      ├── step_reasonableness   (0-100)
      └── solution_feasibility  (0-100)
  → 逐场景得分 + 平均分 + 失败率
```

### 3. 五个预定义场景

| 场景 | 故障类型 | 期望根因 |
|------|---------|---------|
| cpu-spike-001 | CPU 突增 95% | 定时任务触发大量计算 |
| memory-leak-001 | 内存持续增长至 85% | 对象未释放，内存泄漏 |
| slow-response-001 | P99 响应 3.5s | 数据库连接池耗尽 |
| disk-full-001 | 磁盘 92% | 日志轮转失效 |
| service-down-001 | Pod CrashLoop | 依赖 Redis 不可达 |

每个场景带了完整的 mock 数据（Prometheus 告警、CPU/内存指标、日志），让 Agent 在接近真实的环境下执行诊断。

### 4. 三维度评分

| 维度 | 含义 | 90+ | 70-89 | <50 |
|------|------|-----|-------|-----|
| root_cause_accuracy | 根因是否定位正确 | 完全正确 | 方向对但不够精确 | 错误 |
| step_reasonableness | 排查步骤是否逻辑清晰 | 严密完整 | 基本合理，少量遗漏 | 混乱 |
| solution_feasibility | 方案是否可操作 | 具体可执行 | 可行但笼统 | 不可行 |

### 5. 关键设计点

- **评审 LLM 用 temperature=0**：消除随机性，保证同一场景多轮评估结果一致
- **结构化输出**：JSON schema 约束评分格式，正则回退解析——AI 输出不可靠时需要容错
- **失败不计平均**：诊断失败（工具崩溃等）不计入平均分——区分"不会诊断"和"诊断不了"
- **结果持久化**：评估结果写 `data/evaluation_results.json`，支持横向对比不同模型/配置

### 6. 评估结果

```
综合得分平均: 84/100
  根因准确度: 77  ← 提升空间最大（受限于 mock 数据精度）
  步骤合理性: 88
  方案可行性: 87
```

---

## 面试话术

> "Skills 是面向运维的 4 个声明式快捷指令，零代码复用了已有的基础设施。评估方面，我们构建了一个 LLM 驱动的 Harness——评审模型设计了 5 个典型故障场景（CPU 飙升、内存泄漏、响应超时、磁盘满、服务宕机），从根因准确度、排查步骤合理性、方案可行性三个维度打分。综合得分 84，其中步骤合理性和方案可行性都在 85 以上，根因准确度是下一步重点提升的方向。"
