# OpsPilot — 具备自演进能力的智能运维 Agent 系统

> 企业级智能运维助手，支持 ReAct 对话、RAG 知识库问答和 AIOps 自动故障诊断，核心亮点是诊断经验自归档的知识闭环机制——系统排查的故障越多，积累的经验越厚，诊断越准。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-latest-orange.svg)](https://www.langchain.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-latest-purple.svg)](https://langchain-ai.github.io/langgraph/)

---

## 核心特性

- **ReAct 智能对话** — 全链路 SSE 流式输出，Agent 自主决策调用知识检索或外部工具链完成信息整合，思考过程实时可见
- **AIOps 诊断闭环** — Plan-Execute-Replan 多步推理引擎实现告警到根因的全流程自动化，诊断完成后自动归档至向量知识库，形成"诊断→归档→检索增强→更优诊断"的自演进正反馈
- **RAG 知识库** — 支持文档上传自动建立向量索引，时间衰减加权 + L2 距离阈值过滤的混合检索策略，减少 LLM 幻觉
- **上下文窗口管理** — 三层上下文管理：全量窗口保留 + LLM 摘要压缩 + 向量归档，跨会话记忆可控
- **会话持久化** — 基于 SQLite 的对话持久化，服务重启不丢失对话历史
- **MCP 工具集成** — 基于 Model Context Protocol 标准化接入日志查询和监控数据工具
- **Skills 扩展** — 诊断触发、告警巡检、知识库管理、会话管理 4 个运维快捷指令
- **评估 Harness** — LLM 驱动的自动化评估框架，从根因准确度、步骤合理性、方案可行性三维度量化诊断能力

---

## 技术栈

- **框架**: FastAPI + LangChain + LangGraph
- **Agent 模式**: ReAct + Plan-Execute-Replan + Function Calling
- **LLM**: 阿里云 DashScope (兼容 OpenAI 协议)
- **Embedding**: DashScope text-embedding-v4 (1024 维)
- **向量库**: Milvus (L2 索引)
- **工具协议**: MCP (Model Context Protocol)
- **会话持久化**: LangGraph AsyncSqliteSaver
- **前端**: 原生 HTML/JS/CSS + SSE + Markdown 实时渲染

---

## 快速开始

### 环境要求

- Python 3.11+
- 阿里云 DashScope API Key ([获取地址](https://bailian.console.aliyun.com/))
- Docker (用于 Milvus 向量数据库)

### 安装和启动 (Linux/macOS)

```bash
# 1. 克隆项目
git clone <repository_url>
cd OpsPilot

# 2. 安装依赖 (推荐使用 uv)
pip install uv
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# 3. 编辑 .env 文件，填入你的 DASHSCOPE_API_KEY
vim .env

# 4. 一键初始化 (启动 Docker + 服务 + 上传文档)
make init

# 5. 一键启动
make start
```

### 访问服务

- **Web 界面**: http://localhost:9900
- **API 文档**: http://localhost:9900/docs
- **Milvus 管理 UI**: http://localhost:8000

---

## 项目结构

```
OpsPilot/
├── app/                                    # 应用核心
│   ├── main.py                             # FastAPI 入口，生命周期管理
│   ├── config.py                           # Pydantic Settings 配置管理
│   ├── api/                                # API 路由层
│   │   ├── chat.py                         # 对话接口 (普通/流式/历史/会话列表)
│   │   ├── aiops.py                        # AIOps 诊断接口 (SSE 流式)
│   │   ├── file.py                         # 文件上传与向量索引
│   │   └── health.py                       # 健康检查
│   ├── services/                           # 业务服务层
│   │   ├── rag_agent_service.py            # ReAct Agent (LangGraph create_agent)
│   │   ├── aiops_service.py                # Plan-Execute-Replan 诊断服务
│   │   ├── diagnosis_archiver.py           # 诊断结果自动归档
│   │   ├── context_manager.py              # 三层上下文管理 Middleware
│   │   ├── evaluation_harness.py           # Agent 评估 Harness
│   │   ├── vector_store_manager.py         # Milvus 向量存储管理
│   │   ├── vector_embedding_service.py     # DashScope Embedding 服务
│   │   ├── vector_index_service.py         # 文档索引服务
│   │   ├── vector_search_service.py        # 向量检索服务
│   │   └── document_splitter_service.py    # Markdown 文档分割
│   ├── agent/                              # Agent 模块
│   │   ├── mcp_client.py                   # MCP 客户端 (单例 + 重试拦截器)
│   │   └── aiops/                          # AIOps 三节点
│   │       ├── state.py                    # PlanExecuteState 状态定义
│   │       ├── planner.py                  # Planner: 制定诊断计划
│   │       ├── executor.py                 # Executor: 执行工具调用
│   │       ├── replanner.py                # Replanner: 评估 + 重规划
│   │       └── utils.py                    # 工具格式化
│   ├── tools/                              # Agent 工具集
│   │   ├── knowledge_tool.py               # 知识检索 (L2 阈值 + 时间衰减)
│   │   ├── query_metrics_alerts.py         # Prometheus 告警查询
│   │   └── time_tool.py                    # 时间查询
│   ├── core/                               # 核心组件
│   │   ├── checkpoint_manager.py           # AsyncSqliteSaver 管理器
│   │   ├── llm_factory.py                  # LLM 工厂 (OpenAI 兼容模式)
│   │   └── milvus_client.py                # Milvus 客户端
│   ├── models/                             # Pydantic 数据模型
│   └── utils/                              # 工具类 (Loguru 日志)
├── static/                                 # Web 前端
│   ├── index.html                          # 主页面
│   ├── app.js                              # 前端逻辑 (SSE + Markdown 渲染)
│   └── styles.css                          # 样式
├── mcp_servers/                            # MCP 服务
│   ├── cls_server.py                       # CLS 日志查询 (端口 8003)
│   └── monitor_server.py                   # 监控数据查询 (端口 8004)
├── .claude/skills/                         # Claude Code Skills
│   ├── ops-diagnose.md                     # 一键触发 AIOps 诊断
│   ├── ops-alerts.md                       # 快速查询 Prometheus 告警
│   ├── ops-knowledge.md                    # 知识库管理
│   └── ops-sessions.md                     # 会话管理
├── aiops-docs/                             # 运维知识库文档
├── data/                                   # 持久化数据
│   ├── checkpoints.db                      # SQLite 对话持久化
│   └── evaluation_results.json             # 评估结果
├── .env                                    # 环境变量配置
├── pyproject.toml                          # 项目配置 & 依赖
├── vector-database.yml                     # Milvus Docker Compose
├── Makefile                                # 项目管理命令
└── README.md
```

---

## 配置说明

通过 `.env` 文件配置：

```bash
# DashScope 配置 (必填)
DASHSCOPE_API_KEY=your-api-key
DASHSCOPE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen3.7-plus

# Milvus 配置
MILVUS_HOST=localhost
MILVUS_PORT=19530

# RAG 配置
RAG_TOP_K=3
RAG_MODEL=qwen3.7-plus

# 文档分块配置
CHUNK_MAX_SIZE=800
CHUNK_OVERLAP=100

# MCP 服务配置
MCP_CLS_TRANSPORT=streamable-http
MCP_CLS_URL=http://localhost:8003/mcp
MCP_MONITOR_TRANSPORT=streamable-http
MCP_MONITOR_URL=http://localhost:8004/mcp

# Prometheus
PROMETHEUS_BASE_URL=http://127.0.0.1:9090
PROMETHEUS_REQUEST_TIMEOUT=10.0
```

---

## API 接口

| 功能 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 普通对话 | POST | `/api/chat` | ReAct Agent 一次性返回 |
| 流式对话 | POST | `/api/chat_stream` | SSE 逐块推送 |
| AIOps 诊断 | POST | `/api/aiops` | Plan-Execute-Replan 流式诊断 |
| 文件上传 | POST | `/api/upload` | 上传并自动向量化索引 |
| 会话历史 | GET | `/api/chat/session/{id}` | 查询指定会话历史 |
| 会话列表 | GET | `/api/chat/sessions` | 列出所有已保存会话 |
| 清空会话 | POST | `/api/chat/clear` | 删除指定会话 |
| 健康检查 | GET | `/health` | 服务 + Milvus 状态 |

### 使用示例

```bash
# 普通对话
curl -X POST http://localhost:9900/api/chat \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"CPU使用率过高怎么排查"}'

# 流式对话
curl -X POST http://localhost:9900/api/chat_stream \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"帮我查一下最近的错误日志"}' \
  --no-buffer

# AIOps 诊断
curl -X POST http://localhost:9900/api/aiops \
  -H "Content-Type: application/json" \
  -d '{"session_id":"diag-001"}' \
  --no-buffer

# 上传运维文档
curl -X POST http://localhost:9900/api/upload \
  -F "file=@cpu_high_usage.md"

# 查看所有会话
curl http://localhost:9900/api/chat/sessions
```

---

## 核心架构详解

### ReAct 智能对话

```
用户问题 → ReAct Agent (ChatQwen + Function Calling)
  → 自主决策调用哪一个工具:
    ├── retrieve_knowledge  → Milvus 向量检索 (L2<1.0 过滤 + 时间衰减排序)
    ├── query_prometheus_alerts → Prometheus HTTP API
    ├── query_cpu_metrics   → MCP Monitor Server
    ├── search_log          → MCP CLS Server
    └── get_current_time    → 时区感知的时间查询
  → SSE 流式推送思考过程 + 工具调用 + 最终答案
```

### AIOps 诊断闭环

```
触发诊断
  → Planner:
      ├── 检索向量库中的历史相似案例
      ├── 获取全部可用工具 (本地 + MCP)
      └── LLM 生成结构化诊断计划
  → Executor:
      ├── 取出 plan 第一个步骤
      ├── Function Calling 自动工具调用循环
      └── 返回 (task, result)
  → Replanner:
      ├── 评估已执行结果
      ├── 决策: continue / replan / respond
      └── 三层安全阀: ≥8步强制结束 / ≥5步禁止replan / 新步骤≤剩余步骤数
  → 诊断完成:
      ├── 最终报告通过 SSE 推送
      └── 异步归档到向量库 (fire-and-forget)
```

### 检索策略

```
query → Embedding → Milvus L2搜索 (fetch_k = top_k × 3)
  → L2距离过滤 (L2 < 1.0, 剔除噪声)
  → 相似度转换 (1 / (1 + L2))
  → 时间衰减加权 (× e^(-0.023 × days))
  → 组合排序 → 返回 TopK
```

### 上下文窗口管理

```
消息 ≤ 16 条 → 不做处理
消息 > 16 条:
  Layer 1: 最近 10 条完整保留
  Layer 2: 溢出消息经 LLM 压缩为 ≤200 字摘要
  Layer 3: 摘要写入 Milvus (context_summary 标签)
  最终消息 = [摘要] + [最近 10 条]
```

---

## Skills 使用

在 Claude Code 中，使用以下斜杠命令触发运维快捷操作：

| 命令 | 功能 |
|------|------|
| `/ops-diagnose` | 一键触发 AIOps 诊断，生成 Markdown 报告并自动归档 |
| `/ops-alerts` | 查询 Prometheus 当前活动告警，按级别分类展示 |
| `/ops-knowledge` | 管理知识库：上传文档、检索内容、查看归档统计 |
| `/ops-sessions` | 查看/清理持久化对话会话 |

---

## 运行评估

```bash
source .venv/bin/activate
python3 -c "
import asyncio
from app.services.evaluation_harness import BUILTIN_SCENARIOS, run_full_evaluation, print_evaluation_summary
results = asyncio.run(run_full_evaluation(BUILTIN_SCENARIOS))
print_evaluation_summary(results)
"
```

评估结果保存在 `data/evaluation_results.json`。

---

## 常用命令

```bash
make init              # 一键初始化 (Docker + 服务 + 文档)
make start             # 启动所有服务
make stop              # 停止所有服务
make restart           # 重启所有服务
make format            # 格式化代码 (black + isort + ruff)
make lint              # 代码检查 (ruff + mypy)
```

---

## 常见问题

### API Key 错误

```bash
# 检查环境变量
cat .env | grep DASHSCOPE_API_KEY
```

### Milvus 连接失败

```bash
# 确保 Docker 已启动
docker ps | grep milvus

# 重启 Milvus
docker compose -f vector-database.yml restart
```

### 服务无法启动

```bash
# 查看服务日志
tail -f logs/app_$(date +%Y-%m-%d).log

# 检查端口占用
lsof -i :9900  # FastAPI
lsof -i :8003  # CLS MCP
lsof -i :8004  # Monitor MCP
```

---

## 参考资源

- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [LangChain 文档](https://python.langchain.com/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [LangGraph Plan-Execute 教程](https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/)
- [阿里云 DashScope](https://help.aliyun.com/zh/model-studio/)
- [MCP 协议](https://modelcontextprotocol.io/)
- [Milvus 文档](https://milvus.io/docs)

---

## 许可证

MIT License
