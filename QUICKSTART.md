# OpsPilot 启动与关闭指南

## 启动

按顺序执行，每一步确认成功后再进行下一步：

### 1. 启动 Milvus 向量数据库

```bash
cd /Users/userming/Code/OpsPilot
docker compose -f vector-database.yml --env-file /dev/null up -d
```

确认（所有容器 STATUS 为 healthy）：
```bash
docker compose -f vector-database.yml --env-file /dev/null ps
```

### 2. 启动 MCP 服务（两个新终端窗口）

终端 A — CLS 日志查询服务：
```bash
cd /Users/userming/Code/OpsPilot
source .venv/bin/activate
python mcp_servers/cls_server.py
```

终端 B — Monitor 监控数据服务：
```bash
cd /Users/userming/Code/OpsPilot
source .venv/bin/activate
python mcp_servers/monitor_server.py
```

### 3. 启动 FastAPI 主服务

```bash
cd /Users/userming/Code/OpsPilot
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900
```

### 4. （可选）上传知识库文档

```bash
cd /Users/userming/Code/OpsPilot
source .venv/bin/activate
for f in aiops-docs/*.md; do
  curl -s -X POST http://localhost:9900/api/upload -F "file=@$f"
done
```

### 5. 访问

- Web 界面: http://localhost:9900
- API 文档: http://localhost:9900/docs
- Milvus 管理: http://localhost:8000

---

## 关闭

```bash
# 停止 FastAPI + MCP 服务
lsof -ti:9900 | xargs kill -9
lsof -ti:8003 | xargs kill -9
lsof -ti:8004 | xargs kill -9

# 停止 Milvus
cd /Users/userming/Code/OpsPilot
docker compose -f vector-database.yml --env-file /dev/null down
```

---

## 简化版（一键启动）

如果不想开多个终端，可以全部后台运行：

```bash
cd /Users/userming/Code/OpsPilot
source .venv/bin/activate

# 1. Milvus
docker compose -f vector-database.yml --env-file /dev/null up -d

# 2. MCP 服务（后台）
python mcp_servers/cls_server.py &
python mcp_servers/monitor_server.py &

# 3. FastAPI（后台）
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900 &

# 4. 稍等后验证
sleep 10
curl -s http://localhost:9900/health | python3 -m json.tool
```
