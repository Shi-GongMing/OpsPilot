---
name: ops-diagnose
description: 一键触发 AIOps 智能故障诊断，自动分析系统告警并生成诊断报告
---

## 功能

触发 OpsPilot AIOps 诊断引擎，自动执行 Plan-Execute-Replan 流程：查询 Prometheus 告警 → 检索知识库历史案例 → 调用监控工具分析 → 生成根因分析报告 → 自动归档经验。

## 执行步骤

1. 确认 OpsPilot 服务正在运行（`curl -s http://localhost:9900/health`）
2. 可以指定会话 ID，也可自动生成
3. 调用 `/api/aiops` 接口触发诊断
4. 将返回的 Markdown 报告直接展示给用户，不做二次解析
5. 完成后提醒用户诊断结果已自动归档到知识库

## API

```bash
curl -s -X POST http://localhost:9900/api/aiops \
  -H "Content-Type: application/json" \
  -d '{"session_id":"diag-<timestamp>"}' \
  --no-buffer
```

## 输出格式

直接呈现 SSE 流式返回的诊断报告（Markdown），包含：
- 活跃告警清单
- 根因分析
- 处理方案
- 结论与风险评估
