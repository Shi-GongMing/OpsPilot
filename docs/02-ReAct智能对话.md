# 02 — ReAct 智能对话：Agent 如何"自主决策"

> **面试重点**：`create_agent()` 底层原理、SSE vs WebSocket 协议选型、流式内容提取兼容性
>
> **涉及文件**：`services/rag_agent_service.py`、`api/chat.py`
>
> **核心标准库**：LangGraph `create_agent()`、LangChain `ChatQwen`

---

## 0. 前置：什么时候该用 Agent？

**判断标准**：路径是否可预定义。

| 特征 | 用 Workflow/Chain | 用 Agent |
|------|:---:|:---:|
| 下一步做什么 | 代码写死了 | LLM 根据上下文判断 |
| 工具调用顺序 | 固定的 | 动态的 |
| 例子 | 文件上传→分块→存库 | "帮我查昨晚data-sync有没有报错" |

文档上传在我们的项目里是固定三步 pipeline，用 Chain 串联。运维问答每次的检索路径和工具选择都不同，必须 Agent 自主决策。这是"什么时候用 Agent"的标准答案。

---

## 1. `create_agent()` 底层到底做了什么

LangGraph 的 `create_agent()` 是预构建 Agent 工厂——一行代码在内部自动完成四件事：

**① 构建 StateGraph**：自动创建状态图，状态 schema 是 `AgentState`（含 `messages` 字段，使用 `add_messages` reducer）。reducer 的含义是"追加"而非"覆盖"——每轮对话的消息累积到历史中。

**② 注册 model_node**：把传入的 LLM 包装成模型节点。这个节点的逻辑是"把当前消息历史发给 LLM，获取回复"。LLM 回复可能是纯文本（任务完成）或含 `tool_calls`（需要调工具）。

**③ 注册 tools_node 和条件边**：根据工具列表创建工具执行节点，设置条件路由：

```
model_node → 有 tool_calls? → tools_node → model_node (循环)
           → 无 tool_calls? → END
```

这就是 ReAct 模式的核心——Thought（LLM 决策）→ Action（执行工具）→ Observation（看结果）→ Thought（继续推理）。

**④ 注入 checkpointer 和 middleware**：如果传了 checkpointer，每次 model_node 执行前后自动读写 checkpoint。传了 middleware，在每次模型调用前触发钩子。

### 我们为什么对话用 create_agent()，诊断用手写 StateGraph

对话是标准 ReAct 循环，用预制件正好。诊断需要 Planner→Executor→Replanner 自定义三节点拓扑——LangGraph 没有对应的预制件，只能手写。**标准行为用预制件，定制拓扑自己写**——这是架构选型的通用原则。

### [自研概念] StateGraph / reducer / ReAct 模式

---

## 2. SSE vs WebSocket（协议选型深度分析）

**这是面试中最能体现基础扎实程度的决策点。**

### 协议层根本差异

SSE 建立在 HTTP/1.1 之上——就是一次普通的 GET 请求，响应头带 `Content-Type: text/event-stream` 和 `Connection: keep-alive`。此后服务端持续往这个 TCP 连接写数据，数据格式是纯文本 `data: <payload>\n\n`。**全程没有协议升级。**

WebSocket 需要 HTTP Upgrade 握手——客户端发 `Upgrade: websocket`，服务端回 `101 Switching Protocols`，此后脱离 HTTP，转向 WebSocket 帧协议（二进制帧，支持分片、掩码、控制帧）。

### 全双工 vs 半双工

| 概念 | 含义 | 类比 |
|------|------|------|
| 单工 | 只能 A→B | 广播电台 |
| 半双工 | 双方可交替发送，但不能同时 | 对讲机 |
| 全双工 | 双方同时独立发送 | 电话 |

HTTP 的请求-响应模型本质是半双工。SSE 把这个模型变成"客户端发一次请求，服务端持续推送"。WebSocket 升级后脱离 HTTP，建立全双工 TCP 通道。

**Agent 对话场景**：用户发消息→Agent 思考→流式输出答案。在这个过程中**用户被输入框禁用，无法发新消息**。这是天然的**单向推送**场景——全双工的 WebSocket 在这个场景下是冗余的。

### 工程代价对比

| 维度 | SSE | WebSocket |
|------|-----|-----------|
| 连接建立 | 一次 HTTP GET | Upgrade 握手（多一次往返） |
| 断线重连 | 浏览器 `EventSource` 自动重连 | 需手写 onclose + 退避延迟 + 重连状态机 |
| 代理穿透 | HTTP 代理零配置支持 | Nginx 需配 `proxy_http_version 1.1` + Upgrade 头 |
| 调试 | `curl -N` 直接看纯文本 | 需要 wscat 或 DevTools |
| 服务端实现 | `EventSourceResponse(generator)` 一行 | 需 `await websocket.accept()` + 手动循环 |

### 面试一句话

> "SSE 和 WebSocket 的本质区别不是'能不能实时'——两者都能实时推送。区别在于 HTTP 长连接 vs 协议升级后的全双工通道。Agent 流式是天然单向推送场景，用 SSE 刚好够用——不需要 WebSocket 的 Upgrade 握手、心跳保活、手动重连这些额外复杂度。技术选型的核心原则是用刚好够用的方案，不引入不需要的复杂性。"

### [自研概念] SSE / WebSocket / 全双工通信

---

## 3. 延迟优化：关闭思维链

千问 3.7-plus 默认开启思维链（Chain-of-Thought）。模型在输出答案前先产生一批内部推理 token，流式输出时以 `reasoning` 块推送。

**实测数据**：

| 指标 | thinking 开启 | thinking 关闭 |
|------|:---:|:---:|
| 首字可见延迟 | ~10s | ~1s |
| 空 reasoning 块 | 32 个 | 0 |
| 额外 token | +27/次 | 0 |

**为什么关**：运维问答的核心是"查到正确的数据"而非"深度数学推理"。thinking 的 32 个空推理块不增值，反而增加延迟和成本。

### 面试一句话

> "我们关了思维链——实测首字延迟从 10 秒降到 1 秒，每次省 27 个 token。运维场景不需要深度推理，'够好'就是工程标准。这背后体现的是生产环境的成本意识和用户体验意识。"

---

## 4. 流式内容提取的兼容性设计

**工程问题**：不同 LLM 的流式返回结构不一致。

千问（通过 `ChatQwen`）返回的 token 带 `content_blocks` 属性——思考内容、文本输出和工具调用分类打包。OpenAI 原生模型把文本直接放 `content` 字符串。

**兼容方案**：在流式回调中同时检查两个属性——优先读 `content_blocks`（千问规范路径），不存在则回退到 `content`。这样换模型提供商时核心逻辑无需改动。

从 `content_blocks` 中提取三类块：
- `type == "text"` → 推送文本内容给前端
- `type == "tool_call_chunk"` → 推送工具调用事件
- `type == "reasoning"` → 忽略（已关 thinking）

---

## 5. 容错设计：MCP 降级

Agent 初始化时会连接 MCP 服务加载远程工具。如果 MCP 不可达，不会抛异常——返回空工具列表，Agent 用 3 个本地工具（知识检索、告警查询、时间）继续运行。核心原则：**Agent 能力可有损降级，不能整体不可用。**

---

## 涉及的文件与职责

| 文件 | 职责 |
|------|------|
| `services/rag_agent_service.py` | Agent 创建、异步延迟初始化、流式/非流式对话、session 管理 |
| `api/chat.py` | HTTP 路由、SSE 事件封装、`/chat/sessions` 会话列表接口 |

---

## 面试话术

> "我们先做了选型判断——运维问答路径不可预定义，必须用 Agent。框架选了 LangGraph 的 `create_agent()`——它内部自动构建了 StateGraph，注册了 model_node 和 tools_node，用条件边实现 ReAct 循环。通信协议选了 SSE——Agent 流式是天然的单向推送，SSE 基于 HTTP，浏览器自动重连，代理零配置，WebSocket 的全双工在这里是冗余的。延迟上关了千问的思维链，实测首字从 10 秒降到 1 秒。容错上 MCP 工具加载失败时自动降级为纯本地工具——Agent 不会整体不可用。"
