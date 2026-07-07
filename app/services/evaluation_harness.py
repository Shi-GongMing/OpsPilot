"""
Agent 评估 Harness

模拟故障场景 → Agent 诊断 → 多维度自动评分

架构：
  ScenarioGenerator (LLM) → MockToolProvider → AIOpsService.execute()
      → EvaluationJudge (LLM) → 评分报告
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class FaultScenario:
    """评估用故障场景"""

    scenario_id: str
    title: str
    description: str  # 给 Agent 的诊断任务描述
    expected_root_cause: str  # 期望的根因结论
    expected_steps: list[str]  # 期望的排查步骤关键词

    # 模拟数据
    mock_prometheus_alerts: list[dict[str, Any]] = field(default_factory=list)
    mock_cpu_metrics: dict[str, Any] = field(default_factory=dict)
    mock_memory_metrics: dict[str, Any] = field(default_factory=dict)
    mock_logs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    """评估结果"""

    scenario_id: str
    scenario_title: str

    # 评分 (0-100)
    root_cause_accuracy: int
    step_reasonableness: int
    solution_feasibility: int

    overall_score: int  # 三维度平均

    judge_comments: str = ""
    agent_response_preview: str = ""

    errors: list[str] = field(default_factory=list)


# ============================================================
# 预定义故障场景（基于 aiops-docs 中的真实故障模式）
# ============================================================

BUILTIN_SCENARIOS: list[FaultScenario] = [
    FaultScenario(
        scenario_id="cpu-spike-001",
        title="CPU 使用率突增至 95%",
        description="服务 data-sync-service 的 CPU 使用率在最近 30 分钟内从 10% 突增至 95%，触发了 HighCPUUsage 告警（级别：严重）。请分析原因并给出处理建议。",
        expected_root_cause="定时任务或批处理任务触发大量计算，导致 CPU 使用率飙升",
        expected_steps=["CPU使用率", "监控数据", "日志查询", "进程检查", "top"],
        mock_prometheus_alerts=[
            {
                "alert_name": "HighCPUUsage",
                "state": "firing",
                "labels": {"severity": "critical", "instance": "data-sync-service-01", "alertname": "HighCPUUsage"},
                "annotations": {"summary": "data-sync-service CPU 使用率过高", "description": "CPU 使用率持续 5 分钟超过 80% 阈值，当前值 95.2%"},
                "active_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        mock_cpu_metrics={
            "service_name": "data-sync-service",
            "data_points": [
                {"timestamp": "10:00", "value": 12.5},
                {"timestamp": "10:05", "value": 15.3},
                {"timestamp": "10:10", "value": 45.8},
                {"timestamp": "10:15", "value": 78.2},
                {"timestamp": "10:20", "value": 92.1},
                {"timestamp": "10:25", "value": 95.2},
            ],
            "statistics": {"avg": 56.5, "max": 95.2, "min": 12.5, "p95": 94.5, "spike_detected": True},
        },
        mock_logs={
            "topic-001": [
                {
                    "timestamp": "10:15:00",
                    "level": "INFO",
                    "message": "定时任务 batch-sync-job 开始执行，处理 50000 条记录",
                },
                {
                    "timestamp": "10:20:00",
                    "level": "WARN",
                    "message": "Thread pool exhausted: active=200, queue=500",
                },
                {
                    "timestamp": "10:22:00",
                    "level": "ERROR",
                    "message": "Task batch-sync-job timeout after 420s, partial completion: 48200/50000",
                },
            ]
        },
    ),
    FaultScenario(
        scenario_id="memory-leak-001",
        title="data-sync-service 内存持续增长",
        description="服务 data-sync-service 的内存使用率在过去 1 小时内从 30% 持续增长至 85%，期间服务负载无明显变化。GC 日志显示 Full GC 频率从每小时 2 次增加到每 10 分钟 1 次。请分析原因。",
        expected_root_cause="应用存在内存泄漏，可能是对象未正确释放或缓存未设置过期策略",
        expected_steps=["内存使用率", "GC", "内存泄漏", "堆分析", "dump"],
        mock_prometheus_alerts=[
            {
                "alert_name": "HighMemoryUsage",
                "state": "firing",
                "labels": {"severity": "warning", "instance": "data-sync-service-02", "alertname": "HighMemoryUsage"},
                "annotations": {"summary": "data-sync-service 内存使用率高于 85%", "description": "内存使用率持续增长，当前 85.3%，超过 warning 阈值 70%"},
                "active_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        mock_memory_metrics={
            "service_name": "data-sync-service",
            "data_points": [
                {"timestamp": "09:00", "value": 32.1, "used_gb": 2.6, "total_gb": 8.0},
                {"timestamp": "09:15", "value": 45.0, "used_gb": 3.6, "total_gb": 8.0},
                {"timestamp": "09:30", "value": 58.3, "used_gb": 4.7, "total_gb": 8.0},
                {"timestamp": "09:45", "value": 72.1, "used_gb": 5.8, "total_gb": 8.0},
                {"timestamp": "10:00", "value": 85.3, "used_gb": 6.8, "total_gb": 8.0},
            ],
            "statistics": {"avg": 58.6, "max": 85.3, "min": 32.1, "memory_pressure": True},
        },
        mock_logs={
            "topic-001": [
                {
                    "timestamp": "09:30:00",
                    "level": "WARN",
                    "message": "GC overhead limit exceeded, Full GC took 12.5s",
                },
                {
                    "timestamp": "09:40:00",
                    "level": "WARN",
                    "message": "GC overhead limit exceeded, Full GC took 18.3s",
                },
                {
                    "timestamp": "09:55:00",
                    "level": "ERROR",
                    "message": "java.lang.OutOfMemoryError: Java heap space in DataCache$LruMap",
                },
            ]
        },
    ),
    FaultScenario(
        scenario_id="slow-response-001",
        title="api-gateway 服务响应时间超过 3 秒",
        description="api-gateway-service 的 P99 响应时间从正常的 200ms 增加到 3500ms。同时段内请求量无明显变化。数据库连接池使用率从 30% 升至 95%。请分析原因。",
        expected_root_cause="数据库连接池耗尽或存在慢 SQL，导致请求排队等待数据库连接",
        expected_steps=["响应时间", "数据库", "连接池", "慢查询", "SQL"],
        mock_prometheus_alerts=[
            {
                "alert_name": "HighResponseTime",
                "state": "firing",
                "labels": {"severity": "critical", "instance": "api-gateway-service-01", "alertname": "HighResponseTime"},
                "annotations": {"summary": "API 响应时间过长", "description": "P99 响应时间 3500ms，超过 1000ms 阈值"},
                "active_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        mock_logs={
            "topic-003": [
                {"timestamp": "10:30:00", "level": "WARN", "message": "Connection pool exhausted: active=95/100, waiting=45"},
                {"timestamp": "10:31:00", "level": "WARN", "message": "Slow query detected: SELECT * FROM orders WHERE ... took 8.2s"},
                {"timestamp": "10:32:00", "level": "ERROR", "message": "HikariPool connection timeout after 30000ms"},
            ]
        },
    ),
    FaultScenario(
        scenario_id="disk-full-001",
        title="服务器磁盘使用率超过 90%",
        description="生产服务器磁盘使用率已达到 92%，主要是 /var/log 目录占用了 45GB 空间。日志轮转配置似乎失效。请分析原因并给出清理建议。",
        expected_root_cause="日志轮转配置失效或日志量突增导致磁盘空间被日志文件占满",
        expected_steps=["磁盘使用率", "日志", "轮转", "清理", "logrotate", "/var/log"],
        mock_prometheus_alerts=[
            {
                "alert_name": "HighDiskUsage",
                "state": "firing",
                "labels": {"severity": "critical", "instance": "prod-server-01", "alertname": "HighDiskUsage"},
                "annotations": {"summary": "磁盘使用率超过 90%", "description": "/dev/sda1 使用率 92%，剩余空间不足 8GB"},
                "active_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        mock_logs={
            "topic-001": [
                {"timestamp": "08:00:00", "level": "ERROR", "message": "logrotate: failed to rotate /var/log/app/*.log: permission denied"},
                {"timestamp": "08:01:00", "level": "WARN", "message": "Disk usage on /dev/sda1 exceeded 90% threshold"},
                {"timestamp": "08:05:00", "level": "ERROR", "message": "Application failed to write log: No space left on device"},
            ]
        },
    ),
    FaultScenario(
        scenario_id="service-down-001",
        title="data-sync-service Pod 频繁重启",
        description="data-sync-service 的 Kubernetes Pod 在过去 15 分钟内重启了 3 次。每次重启前 health check 返回 503。服务启动后约 2 分钟即宕机。请分析原因。",
        expected_root_cause="服务启动后健康检查未能在超时时间内通过（可能启动时间 > liveness probe 超时，或依赖服务不可用导致初始化失败）",
        expected_steps=["重启", "503", "健康检查", "启动", "依赖", "probe"],
        mock_prometheus_alerts=[
            {
                "alert_name": "KubePodCrashLooping",
                "state": "firing",
                "labels": {"severity": "critical", "instance": "data-sync-service-pod-3", "alertname": "KubePodCrashLooping", "namespace": "production", "pod": "data-sync-service-7d8f9-abcde"},
                "annotations": {"summary": "Pod data-sync-service 处于 CrashLoopBackOff", "description": "Pod 在过去 15 分钟内重启了 3 次"},
                "active_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        mock_logs={
            "topic-001": [
                {"timestamp": "09:30:00", "level": "INFO", "message": "Starting data-sync-service v2.3.1"},
                {"timestamp": "09:30:05", "level": "ERROR", "message": "Failed to connect to Redis at redis-cluster:6379: Connection refused"},
                {"timestamp": "09:30:10", "level": "FATAL", "message": "Dependency initialization failed, shutting down"},
            ]
        },
    ),
]


async def build_scenario_overrides(scenario: FaultScenario) -> dict[str, Any]:
    """将场景的 mock 数据转换为 AIOps 诊断可直接使用的上下文"""
    return {
        "场景ID": scenario.scenario_id,
        "场景标题": scenario.title,
        "期望根因": scenario.expected_root_cause,
        "期望排查关键词": scenario.expected_steps,
    }


def _parse_score_from_response(response: str) -> tuple[int, int, int]:
    """从评估模型的 JSON 回复中提取三个维度的分数"""
    try:
        data = json.loads(response)
        return (
            int(data.get("root_cause_accuracy", 0)),
            int(data.get("step_reasonableness", 0)),
            int(data.get("solution_feasibility", 0)),
        )
    except (json.JSONDecodeError, ValueError, KeyError):
        pass

    # 回退：正则匹配
    def _extract(key: str) -> int:
        m = re.search(rf'"{key}"\s*:\s*(\d+)', response)
        if m:
            return int(m.group(1))
        m = re.search(rf"{key}[:\s]*(\d+)", response, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    return (_extract("root_cause_accuracy"), _extract("step_reasonableness"), _extract("solution_feasibility"))


async def evaluate_diagnosis(
    scenario: FaultScenario,
    agent_response: str,
    judge_model: str = "",
) -> EvaluationResult:
    """使用评审模型对 Agent 诊断结果进行多维度评分"""
    from app.config import config
    from langchain_qwq import ChatQwen

    model_name = judge_model or config.rag_model
    judge = ChatQwen(
        model=model_name,
        api_key=config.dashscope_api_key,
        temperature=0.0,
        streaming=False,
        enable_thinking=False,
    )

    eval_prompt = f"""你是一个严格的运维诊断评审专家。请根据以下信息对 AI Agent 的诊断结果进行评分。

## 故障场景
- **场景标题**: {scenario.title}
- **场景描述**: {scenario.description}
- **期望根因**: {scenario.expected_root_cause}
- **期望排查方向**: {', '.join(scenario.expected_steps)}

## Agent 诊断报告
{agent_response[:3000]}

## 评分维度 (每项 0-100 分)
1. **root_cause_accuracy** — 根因定位准确度：Agent 是否识别出正确的根本原因？
   - 90-100: 根因判断完全正确或基本正确
   - 70-89: 根因方向正确但不够精确
   - 50-69: 部分相关但未触及核心
   - <50: 根因判断错误

2. **step_reasonableness** — 排查步骤合理性：Agent 的排查步骤是否逻辑清晰、符合运维最佳实践？
   - 90-100: 步骤完整且逻辑严密
   - 70-89: 主要步骤合理，有少量遗漏
   - 50-69: 步骤有大方向但缺乏细节
   - <50: 步骤混乱或不合逻辑

3. **solution_feasibility** — 方案可行性：Agent 建议的处理方案是否实际可操作？
   - 90-100: 方案具体可执行，有明确操作步骤
   - 70-89: 方案可行但不够具体
   - 50-69: 方案笼统，缺乏可操作性
   - <50: 方案不可行或有误导性

请严格按以下 JSON 格式输出评分结果，不要输出其他内容：
```json
{{
    "root_cause_accuracy": <分数>,
    "step_reasonableness": <分数>,
    "solution_feasibility": <分数>,
    "comments": "<简短评语，一句话>"
}}
```"""

    try:
        response = await judge.ainvoke(eval_prompt)
        response_text = str(response.content) if hasattr(response, 'content') else str(response)

        scores = _parse_score_from_response(response_text)

        return EvaluationResult(
            scenario_id=scenario.scenario_id,
            scenario_title=scenario.title,
            root_cause_accuracy=scores[0],
            step_reasonableness=scores[1],
            solution_feasibility=scores[2],
            overall_score=sum(scores) // 3,
            agent_response_preview=agent_response[:500],
            judge_comments=response_text[:200],
        )
    except Exception as e:
        return EvaluationResult(
            scenario_id=scenario.scenario_id,
            scenario_title=scenario.title,
            root_cause_accuracy=0,
            step_reasonableness=0,
            solution_feasibility=0,
            overall_score=0,
            errors=[f"评估失败: {str(e)}"],
            agent_response_preview=agent_response[:500],
        )


async def run_scenario(scenario: FaultScenario) -> tuple[str, list[str]]:
    """
    对单个场景执行诊断并返回 Agent 报告。

    返回 (report, errors)。
    """
    from app.services.aiops_service import aiops_service

    report_parts: list[str] = []
    errors: list[str] = []

    try:
        async for event in aiops_service.execute(
            user_input=f"请诊断以下问题：{scenario.description}",
            session_id=f"eval-{scenario.scenario_id}",
        ):
            if event.get("type") == "complete":
                report_parts.append(event.get("response", ""))
            elif event.get("type") == "error":
                errors.append(str(event.get("message", "")))
            elif event.get("type") == "report":
                report_parts.append(event.get("report", ""))
    except Exception as e:
        errors.append(f"诊断执行异常: {str(e)}")

    return "\n".join(report_parts), errors


async def run_full_evaluation(
    scenarios: list[FaultScenario] | None = None,
    judge_model: str = "",
) -> list[EvaluationResult]:
    """
    完整的评估流水线：场景列表 → 逐场景诊断 → 多维度评分

    Args:
        scenarios: 要评估的场景列表，默认使用 BUILTIN_SCENARIOS
        judge_model: 评审 LLM 模型名，默认使用配置中的 rag_model

    Returns:
        每个场景的评估结果列表
    """
    if scenarios is None:
        scenarios = list(BUILTIN_SCENARIOS)

    results: list[EvaluationResult] = []

    for i, scenario in enumerate(scenarios):
        print(f"[{i + 1}/{len(scenarios)}] 评估场景: {scenario.title}")

        report, errors = await run_scenario(scenario)

        if errors and not report:
            results.append(
                EvaluationResult(
                    scenario_id=scenario.scenario_id,
                    scenario_title=scenario.title,
                    root_cause_accuracy=0,
                    step_reasonableness=0,
                    solution_feasibility=0,
                    overall_score=0,
                    errors=errors,
                )
            )
            continue

        result = await evaluate_diagnosis(scenario, report, judge_model=judge_model)
        if errors:
            result.errors.extend(errors)
        results.append(result)

    return results


def print_evaluation_summary(results: list[EvaluationResult]):
    """打印评估汇总报告"""
    print("\n" + "=" * 70)
    print("  OpsPilot Agent 评估报告")
    print("=" * 70)
    print(f"  评估场景数: {len(results)}")
    print(f"  评估时间:   {datetime.now().isoformat()}")
    print("-" * 70)

    total_root_cause = 0
    total_steps = 0
    total_solution = 0
    total_overall = 0
    count = len(results)

    valid = [r for r in results if not r.errors]
    failed = len(results) - len(valid)
    count = max(len(valid), 1)

    for r in results:
        print(f"\n  [{r.scenario_id}] {r.scenario_title}")
        if r.errors:
            print(f"    ❌ 诊断失败: {', '.join(r.errors)}")
            continue
        print(f"    根因准确度: {r.root_cause_accuracy:>3}")
        print(f"    步骤合理性: {r.step_reasonableness:>3}")
        print(f"    方案可行性: {r.solution_feasibility:>3}")
        print(f"    综合得分:   {r.overall_score:>3}")
        total_root_cause += r.root_cause_accuracy
        total_steps += r.step_reasonableness
        total_solution += r.solution_feasibility
        total_overall += r.overall_score

    print("\n" + "-" * 70)
    print(f"  有效场景: {len(valid)}, 失败: {failed}")
    print(f"  根因准确度 平均: {total_root_cause // count}")
    print(f"  步骤合理性 平均: {total_steps // count}")
    print(f"  方案可行性 平均: {total_solution // count}")
    print(f"  综合得分   平均: {total_overall // count}")
    print("=" * 70)
