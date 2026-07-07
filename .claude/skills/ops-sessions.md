---
name: ops-sessions
description: 管理 OpsPilot 对话会话：列出历史会话、查看对话内容、清理过期会话
---

## 功能

管理 OpsPilot 的持久化对话会话，支持：
- **列表**：列出所有已保存的会话及其消息数量
- **查看**：读取指定会话的完整对话历史
- **清理**：删除指定会话或清空所有过期会话

## 执行步骤

### 列出所有会话
```bash
curl -s http://localhost:9900/api/chat/sessions | python3 -m json.tool
```

### 查看指定会话历史
```bash
curl -s http://localhost:9900/api/chat/session/<session_id> | python3 -m json.tool
```

### 清空指定会话
```bash
curl -s -X POST http://localhost:9900/api/chat/clear \
  -H "Content-Type: application/json" \
  -d '{"sessionId":"<session_id>"}'
```

## 输出格式

会话列表以表格展示：会话 ID | 消息数 | 类型（诊断/对话）
对话历史按时间线展示用户-助手交替消息。
