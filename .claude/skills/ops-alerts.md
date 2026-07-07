---
name: ops-alerts
description: 快速查询 Prometheus 当前活动告警，按级别分类展示
---

## 功能

查询 Prometheus 服务端当前所有活动告警（firing/pending 状态），按严重级别分类汇总，帮助运维工程师快速掌握系统健康状况。

## 执行步骤

1. 确认 OpsPilot 服务和 MCP 服务均运行中
2. 调用 OpsPilot 对话接口，让 Agent 调用 `query_prometheus_alerts` 工具
3. 解析返回结果，分类展示告警列表
4. 如果 Prometheus 不可达，明确告知用户并提供排查建议

## API

```bash
curl -s -X POST http://localhost:9900/api/chat \
  -H "Content-Type: application/json" \
  -d '{"Id":"alerts-<timestamp>","Question":"查询当前系统所有活动告警，按级别分类汇总"}'
```

## 输出格式

```
🚨 活跃告警概览
├── firing: N 条
├── pending: M 条
└── 上次更新: <timestamp>

严重告警列表:
| 告警名称 | 目标服务 | 持续时间 | 摘要 |
|---------|---------|---------|------|
| ...     | ...     | ...     | ...  |
```
