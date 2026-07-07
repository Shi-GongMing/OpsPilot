---
name: ops-knowledge
description: 管理 OpsPilot 知识库：上传文档、检索内容、查看归档统计
---

## 功能

管理 OpsPilot 向量知识库，支持三种操作：
- **上传**：将本地 Markdown/TXT 运维文档上传并自动向量化索引
- **检索**：语义搜索知识库，查看匹配的故障案例和解决方案
- **统计**：查看知识库中手动上传文档和自动归档案例的数量

## 执行步骤

1. 确认 OpsPilot 服务运行中（`http://localhost:9900/health`）
2. 根据用户意图选择操作：

### 上传文档
```bash
curl -s -X POST http://localhost:9900/api/upload \
  -F "file=@<文件路径>"
```

### 检索知识
```bash
curl -s -X POST http://localhost:9900/api/chat \
  -H "Content-Type: application/json" \
  -d '{"Id":"kb-<timestamp>","Question":"检索：<查询内容>"}'
```

### 查看统计
查询 Milvus 中 `_source_type` 分布：`manual_upload` vs `auto_archive` vs `context_summary`

## 输出格式

检索结果列出 Top3 匹配文档：来源文件、标题、相似度分数、内容预览。
统计结果展示各类文档数量和占比。
