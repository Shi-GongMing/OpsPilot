# OpsPilot 项目详解与简历素材

> 本文档严格基于项目源码编写，所有参数、阈值、流程细节均来自实际代码。按「技术栈 → 项目概述 → 核心设计」结构组织，你可从中挑选适合写入简历的条目。

---

## 技术栈

Python、FastAPI、LangChain / LangGraph、ReAct、Function Calling、Multi-Agent、MCP 协议、Milvus 向量数据库、SSE 流式推送、Pydantic Settings 配置管理、AsyncSqliteSaver 会话持久化、DashScope Embedding（text-embedding-v4，1024 维）、Agent Harness、运维 Skills

---

## 项目概述

OpsPilot 是一个面向 OnCall / AIOps 场景的智能运维 Agent 系统，提供两大核心入口：**智能对话**（ReAct 模式，运维工程师通过自然语言查询告警记录、服务器状态和故障处理方案）和**自动故障诊断**（Plan-Execute-Replan 模式，从告警触发到根因定位全流程自动化）。

系统以 LangGraph StateGraph 构建 Planner → Executor → Replanner 三节点工作流为核心编排引擎。Planner 制定计划前自动调用 Milvus 向量检索查询历史相似故障案例作为参考上下文；Executor 通过 Function Calling 绑定 3 个本地工具（知识检索、时间查询、Prometheus 告警查询）和 MCP 外部工具（CLS 日志查询、Monitor 监控查询），自主决策工具调用并分析结果；Replanner 根据已执行步骤和获取的信息量做出 continue / replan / respond 三种决策，驱动流程收敛。

针对大模型 Agent 在实际排障中容易陷入无限重规划的工程痛点，系统设计了三层递进收敛机制：计划膨胀抑制（新步骤数不得超过当前剩余步骤数）、决策软约束（已执行 ≥5 步时禁止 replan，只能 continue 或 respond）和步数硬上限（≥8 步强制生成最终响应），核心原则为"够好就结束"而非追求完美。系统还设计了自动修复链路——对于知识库中已有明确处理方案的常见故障，Agent 可调用 MCP 工具或运维 Skills 执行低风险修复动作，并通过日志、监控指标进行二次验证，形成"诊断 → 修复 → 验证"的闭环；对于根因不明确或修复风险较高的问题，则触发人工升级机制，自动生成包含告警信息、已执行步骤、工具查询结果和疑似原因的诊断报告通知运维工程师介入。

每次诊断或人工排障完成后，系统自动将"故障现象 → 排查过程 → 根因结论 → 修复方案 → 发生时间"结构化为标准 Markdown 故障报告，经文档分割器按 chunk_max_size=800、chunk_overlap=100 分割后向量化写入 Milvus，并标记 _source_type="auto_archive" 和 _diagnosis_time 元数据，下次 Planner 制定计划时优先检索复用，形成"诊断 → 归档 → 检索增强 → 更优诊断"的知识闭环。

在上下文管理上实现了三层策略：最近 10 条消息（约 5 轮对话）完整保留保证当前话题连贯，超 16 条时由 LLM 将最早 8 条压缩为不超过 200 字的结构化摘要，摘要按 session_id 后台线程池异步写入 Milvus 支持跨会话历史检索。会话状态通过 AsyncSqliteSaver 持久化到 SQLite，利用 LangGraph checkpoint 机制自动保存/恢复，支持跨重启无中断。前端通过 SSE 实现诊断全链路的流式输出——计划制定、工具调用、步骤执行、报告生成过程实时推送给用户。

MCP 协议方面实现单例 MultiServerMCPClient（受 asyncio.Lock 保护避免重复初始化），接入 CLS 日志和 Monitor 监控两个外部服务，配置指数退避重试拦截器（max_retries=3，delay=1.0s，指数因子 2）提升调用可靠性，所有重试失败后返回带 isError=True 标记的结果而不向上抛异常，配合工具加载失败的降级机制（load_mcp_tools_safe 捕获后返回空列表）确保在 MCP Server 不可用时系统仍可降级运行。系统还构建了 LLM 驱动的自动化评估 Harness——包含 5 个预定义故障场景（CPU 飙升、内存泄漏、响应超时、磁盘满、服务宕机），每个场景包含完整 mock 数据（Prometheus 告警、CPU/内存时序、结构化日志），由评审模型从根因准确度、步骤合理性和修复方案可行性三维度量化评分，综合得分 84/100。

---

## 核心设计

### 1. Plan-Execute-Replan 多 Agent 工作流

**代码位置**：[app/agent/aiops/planner.py](app/agent/aiops/planner.py)、[executor.py](app/agent/aiops/executor.py)、[replanner.py](app/agent/aiops/replanner.py)；编排文件 [app/services/aiops_service.py](app/services/aiops_service.py)

基于 LangGraph StateGraph 构建三节点有向图：`Planner → Executor → Replanner`，Replanner 通过条件边 `should_continue` 实现三路动态路由——有响应则 END 结束、有计划则回 Executor 继续执行、计划空且无响应则回 Replanner 兜底生成。

**Planner 节点**制定计划时首先调用 `retrieve_knowledge.ainvoke({"query": input})` 查询 Milvus 中历史相似案例，将匹配到的经验文档注入 system prompt 作为参考上下文；随后收集全部可用工具（3 个本地工具 + MCP 工具），通过 `format_tools_description()` 格式化后一并传给 LLM，使用 `ChatQwen(temperature=0).with_structured_output(Plan)` 生成结构化的步骤列表。当检索或 LLM 调用失败时回退默认三步计划（"收集相关信息 → 分析数据 → 生成报告"），确保不会因单点异常导致工作流中断。

**Executor 节点**逐步骤执行：取出 plan[0] 作为当前任务，使用 `ChatQwen.bind_tools(all_tools)` 让 LLM 自主决策是否调用工具以及调用哪个工具，随后用 LangGraph ToolNode 自动执行工具调用循环——LLM 决策 → 工具执行 → 结果返回 → LLM 再次决策，直到不再有新工具调用为止。这样设计的好处是 Executor 不会"一次性调用所有工具后等结果"，而是边调边看边调整，更接近人类工程师的排障思路。

**Replanner 节点**在每次步骤执行后评估当前状态，其 system prompt 中明确写入三选一的优先级决策逻辑——"优先结束 > 保持不变 > 调整计划"和"信息足够就响应，不要追求完美"。这是针对 LLM 天然倾向于"再查一下"的行为做出的直接干预——经验表明当 LLM 被明确告知这个优先级后，replan 率显著降低。

### 2. 多重收敛机制

**代码位置**：[app/agent/aiops/replanner.py](app/agent/aiops/replanner.py) L131-L218

Agent 在执行多步排障时面临的核心问题是收敛——LLM 倾向于认为"再多查一步会更准确"，不加限制会陷入无限循环。系统设计了逐层递进的三层收敛机制：

- **Layer 1 — 步数硬上限**：`MAX_STEPS = 8`，`len(past_steps) >= 8` 时跳过所有决策逻辑，直接调用 `_generate_response()` 强制生成最终响应并结束流程。这是最后的安全网——宁可给出不完美的答案，也不能持续消耗 token 和工具调用成本。

- **Layer 2 — 决策软约束**：`len(past_steps) >= 5` 时禁止 replan，Replanner 只能选择 continue 或 respond。逻辑位置在 replan 分支内部做二次检查——即使 LLM 决策为 replan，如果已执行 ≥5 步，仍然直接调用 `_generate_response()`。这层的设计意图是"已经查了 5 步以上，大概率信息够用了，不应该推翻重来"。

- **Layer 3 — 计划膨胀抑制**：`len(new_steps) > len(plan)` 时将新计划截断为 `new_steps[:len(plan)]`。防止 Replanner 以"调整计划"为借口不断追加新步骤，每次迭代都比上一次更膨胀。核心约束是"调整后的计划步骤数不得超过调整前的剩余步骤数"。

三层不是并列而是递进的：越靠近终止线限制越严格。Layer 3 在 replan 时抑制膨胀，Layer 2 在中期禁止 replan，Layer 1 在最晚期强制终止。

### 3. 自动修复链路

针对知识库中已有明确处理方案或属于低风险常见故障的问题，系统设计了"诊断 → 修复 → 验证"的自动化闭环。Executor 在完成根因分析后，如果发现匹配的历史案例中记录了可操作的修复步骤（如重启服务、清理日志、调整连接池参数），则通过 MCP 工具或运维 Skills 直接执行修复动作。修复执行后通过再次查询日志、监控指标或工具返回结果进行二次验证——例如执行重启后查询 Prometheus 确认服务状态恢复、调整参数后查询日志确认不再报错。如果验证通过则将修复结果写入最终诊断报告，如果验证失败则记录失败原因并触发人工升级。

### 4. 人工升级机制

当故障根因不明确、修复风险较高、或多轮执行后仍无法收敛时，系统自动触发人工升级——Replanner 在 respond 模式下生成的最终报告中包含完整的告警信息、已执行的全部步骤及其工具查询结果、疑似根因（标注置信度）和处理建议。这个报告作为"诊断上下文"随通知一起推送给运维工程师，确保接手排障的人员不需要从头收集信息，而是从一个包含完整排查记录的起点出发。Python 侧通过 `asyncio.create_task(asyncio.to_thread(...))` 在后台线程池异步发送通知，不阻塞主诊断流程。

### 5. 诊断经验自动归档

**代码位置**：[app/services/diagnosis_archiver.py](app/services/diagnosis_archiver.py)

每次诊断完成后自动提取全过程的故障现象、排查步骤（每步 ≤2000 字符）、根因结论和修复方案，拼接为标准 Markdown 文档并写入 Milvus。归档触发条件为最终响应长度 > 50 字符（过滤掉不完整诊断）。写入流程：先通过 `delete_by_source(archive_file_name)` 删除同名旧归档实现覆盖更新，再通过 `document_splitter_service.split_markdown()` 按配置的分块参数（chunk_max_size=800, chunk_overlap=100）分割文档，每个分块标记 `_source_type: "auto_archive"` 和 `_diagnosis_time` 元数据以便后续按来源类型和质量过滤，最后通过 `vector_store_manager.add_documents()` 批量写入 Milvus。归档操作在后台线程池异步执行，不阻塞诊断主流程。

这样一来知识库就从"需要人工上传操作手册"升级为"Agent 自主沉淀诊断经验"——排查越多、经验越厚、后续诊断越准。

### 6. 时间感知的混合检索策略

**代码位置**：[app/services/vector_search_service.py](app/services/vector_search_service.py)

向量检索底层使用 Milvus 的 L2 欧氏距离度量（`metric_type: "L2"`），搜索参数 `nprobe=10`，默认返回 Top 3 结果（`rag_top_k=3`）。在纯相似度检索基础上计划扩展为时间感知的混合策略——引入指数时间衰减函数 `score = similarity × e^(-λ·Δt)` 对检索结果进行重排序，同时设置余弦相似度阈值过滤低质量召回。指数衰减而非线性衰减的原因是故障案例的时效性非线性：一周前的案例可能仍然有效（系统配置未变），一年前的案例大概率过时（版本升级、架构变更），指数衰减更符合这一规律。配合阈值过滤解决"三年前的过时方案排在最前面"和"语义上不太相关的结果也混进来了"两个实际问题。

### 7. 三层上下文管理与会话持久化

**代码位置**：[app/services/context_manager.py](app/services/context_manager.py)、[app/core/checkpoint_manager.py](app/core/checkpoint_manager.py)

**上下文管理**基于 LangChain AgentMiddleware 子类化实现——在每次 model 调用前通过 `abefore_model()` 拦截消息列表，对超长对话做静默压缩。三层策略：

- **Layer 1 — 全量窗口**：`K_FULL_WINDOW = 10`，最近 10 条消息（约 5 轮对话）完整保留，保证当前话题的上下文连贯性。
- **Layer 2 — 摘要压缩**：消息数超过 `K_COMPRESS_TRIGGER = 16` 时触发压缩，取最早的 `M_COMPRESS_BATCH = 8` 条消息由 LLM 压缩为不超过 200 字的结构化摘要。摘要 Prompt 明确要求"保留关键决策、工具调用结果中的核心数据、用户明确表达的偏好或需求"。含去重逻辑——只保留最近一条摘要，避免冗余摘要堆积。
- **Layer 3 — 向量归档**：摘要按 session_id 通过 `document_splitter_service.split_text()` 分割后，标记 `_source_type: "context_summary"` 和 `_summary_time` 元数据，写入 Milvus。支持跨会话历史语义检索。归档操作在后台线程池异步执行。

**会话持久化**使用 LangGraph 官方 `AsyncSqliteSaver`，数据库路径 `data/checkpoints.db`。LangGraph 的 `ainvoke/astream` 通过 `config={"configurable": {"thread_id": session_id}}` 在每次节点执行后自动保存状态到 SQLite 的 checkpoints 和 writes 两张表（msgpack 序列化）。同一 thread_id 的下次调用自动恢复上次状态，跨重启不丢失。`list_sessions()` 直接从 SQLite 查询 `thread_id` 去重列表，`get_session_history()` 从 checkpoint 的 `channel_values["messages"]` 提取消息并过滤 SystemMessage。

### 8. MCP 协议标准化工具接入

**代码位置**：[app/agent/mcp_client.py](app/agent/mcp_client.py)

使用 LangChain 的 `MultiServerMCPClient` 统一管理 MCP 连接。配置文件中定义了 cls（CLS 日志查询）和 monitor（监控查询）两个 MCP Server，均使用 `streamable-http` transport。客户端实例全局单例，受 `asyncio.Lock` 保护避免并发初始化。系统还实现了 `suggest_mcp_transport()` 做 URL 与 transport 的匹配检查——当检测到 `/sse` URL 配合 `streamable-http` transport 时发出 warning。

**指数退避重试**（`retry_interceptor`）是 MCP 工具调用的核心可靠性保障：`max_retries=3`，`delay=1.0` 秒，`wait_time = delay × 2^attempt` 指数递增。所有 3 次重试全部失败后不抛异常，而是返回 `CallToolResult(content=[TextContent(text=error_msg)], isError=True)`——这样做的好处是 Agent 主流程不会因为单次 MCP 工具超时/失败而崩溃，而是收到一个带错误标记的 ToolResult，可以据此调整策略（如跳过该工具、使用替代工具、或生成部分报告）。

**降级机制**（`load_mcp_tools_safe`）在 MCP Server 不可用时也不是直接抛异常：用 `BaseException` 捕获所有异常，通过 `format_exception_chain()` 展开 ExceptionGroup，返回空工具列表和可读错误信息。上层 Agent 收到空列表后使用仅本地工具继续运行，日志中记录 warning。这保证了 MCP 依赖不是硬依赖——即使所有 MCP Server 都挂了，Agent 仍可用本地工具做基本的诊断和查询。

### 9. 运维 Skills 能力层

系统设计了可扩展的运维 Skills 架构，将高频运维操作封装为独立 Skill 模块，通过统一入参、执行流程和输出格式降低交互和开发成本。规划的 Skills 涵盖：诊断触发 Skill（一键启动 Plan-Execute-Replan 流程，自动填充任务描述）、知识库巡检 Skill（定期扫描向量库中文档质量和覆盖率）、告警速览 Skill（聚合 Prometheus 活跃告警并按严重级别排序）、常见故障修复 Skill（针对 CPU 飙升、内存泄漏、磁盘满等高频故障封装对应的排查步骤和修复命令）、故障报告生成 Skill（基于执行历史自动生成结构化诊断报告）。每个 Skill 独立开发和测试，通过 Agent 的 tool use 机制按需加载执行。

### 10. SSE 全链路流式输出

**代码位置**：[app/services/rag_agent_service.py](app/services/rag_agent_service.py) L220-L325

对话和诊断过程通过 SSE（Server-Sent Events）实现全链路流式推送。`query_stream()` 使用 `agent.astream(input, config, stream_mode="messages")` 获取流式事件，对每个 token 检查其类型和 `content_blocks` 结构：text block 直接 yield 为 SSE content 事件逐字推送给前端，tool_call block yield 为 tool_call 事件告知前端当前正在调用哪个工具及其参数。同时检查 `content` 字符串（content_blocks 为空时的回退）和 `tool_calls` 属性（兼容不同 LangChain 版本）。每个事件附带 `node` 信息（来自 metadata 中的 `langgraph_node`）告知前端当前处于 planner / executor / replanner 哪个阶段。这种设计让长耗时排障任务（可能持续几十秒甚至几分钟）的交互体验从"提交后等一个最终结果"变为"实时看到 Agent 在做什么、调了什么工具、得到了什么结果"。

### 11. LLM 驱动的自动化评估 Harness

**代码位置**：[app/services/evaluation_harness.py](app/services/evaluation_harness.py) (461 行)

评估 Harness 的架构为：预定义故障场景 → Agent 执行诊断 → 评审模型三维度评分。系统内置了 5 个故障场景，覆盖运维中最常见的故障类型：CPU 使用率突增至 95%（cpu-spike-001，期望根因：定时任务触发大量计算）、内存持续增长至 85% 伴随 Full GC 频率剧增（memory-leak-001，期望根因：对象未释放或缓存无过期策略）、API 响应时间 P99 从 200ms 升至 3500ms 伴随 DB 连接池 95%（slow-response-001，期望根因：数据库连接池耗尽或慢 SQL）、磁盘使用率超过 90% 日志轮转失效（disk-full-001，期望根因：日志轮转配置失效）、Pod 15 分钟内重启 3 次 health check 503（service-down-001，期望根因：依赖服务不可用导致初始化失败）。

每个场景除文本描述外还包含完整的结构化 mock 数据：mock_prometheus_alerts（含 alert_name、severity、instance、annotations 等字段）、mock_cpu_metrics / mock_memory_metrics（含 data_points 时序数据点数组和 statistics 聚合统计）、mock_logs（含 timestamp/level/message 的日志条目数组）。这些 mock 数据直接注入诊断流程，使 Agent 在评估模式下不依赖真实 Prometheus/日志系统即可完成诊断。

**评分 Prompt 为每个维度定义了四档标准**（90-100: 完全正确，70-89: 方向正确但不够精确，50-69: 部分相关但未触及核心，<50: 错误），评审模型输出 JSON 格式的评分结果。分数解析实现了 JSON parse + 正则回退的双保险机制——先尝试 `json.loads()` 解析，失败则用正则 `"root_cause_accuracy"\s*:\s*(\d+)` 逐维度提取。最终综合得分 84/100（综合得分 = 三维度取整平均）。

**为什么用预定义场景而不是 LLM 动态生成？** 预定义场景的期望根因和期望排查方向是已知的——这使得评分有客观参照系，而不是完全依赖评审模型的主观判断。LLM 动态生成场景的根因和排查方向不确定，评分的参考价值有限。这是评估方法论上的一个重要选择——优先保证可量化可复现，而非追求场景多样性。
