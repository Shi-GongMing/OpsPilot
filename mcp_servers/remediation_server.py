"""
运维修复操作 MCP Server

提供安全的运维修复能力，补全"诊断→修复"闭环。
所有工具均包含 dry_run 参数——先预演，确认无误后再实际执行。

设计原则：
  1. 幂等性：同一操作重复执行不会造成二次破坏
  2. 可回滚：关键操作记录执行前的状态，支持撤销
  3. 最小权限：每个工具只做一件事，避免"万能工具"的越权风险
  4. 审计记录：所有操作写日志，包含时间戳、参数、结果
"""

import logging
import functools
import json
from typing import Dict, Any, Optional
from datetime import datetime
from fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Remediation_MCP_Server")

mcp = FastMCP("Remediation")


def log_tool_call(func):
    """装饰器：记录工具调用日志"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.info(f"=" * 60)
        logger.info(f"执行修复操作: {func.__name__}")
        try:
            params_str = json.dumps(kwargs, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            params_str = str(kwargs)
        logger.info(f"参数:\n{params_str}")
        try:
            result = func(*args, **kwargs)
            logger.info(f"结果: SUCCESS")
            logger.info(f"=" * 60)
            return result
        except Exception as e:
            logger.error(f"结果: FAILED - {str(e)}")
            logger.error(f"=" * 60)
            return {"success": False, "error": str(e)}
    return wrapper


# ============================================================
# 1. 服务重启
# ============================================================

@mcp.tool()
@log_tool_call
def restart_service(
    service_name: str,
    method: str = "graceful",
    dry_run: bool = True,
) -> Dict[str, Any]:
    """重启指定的服务（支持 docker/systemd 两种方式）。

    使用场景：CPU/内存异常飙升无法定位根因时的应急止损、应用配置变更后需要重载、
    Pod CrashLoop 后的手动恢复尝试。

    内置安全检查：
    - single_instance：避免在生产环境无脑重启
    - dry_run：先预演确认目标正确再执行
    - method=graceful：优雅重启（SIGTERM → 等待 → SIGKILL），避免直接杀进程导致请求丢失

    Args:
        service_name: 服务名称（必填）
            示例: "data-sync-service", "api-gateway-service"
        method: 重启方式（可选，默认 graceful）
            - "graceful": 优雅重启（先发 SIGTERM，等待 30s 超时后 SIGKILL）
            - "rolling": 滚动重启（K8s 场景，逐个 Pod 替换，保证服务不中断）
            - "force": 强制重启（立即杀进程重启，适合应急场景）
        dry_run: 是否仅预演不实际执行（可选，默认 True）

    Returns:
        Dict: 执行结果
            - success: 是否成功
            - service_name: 服务名称
            - method: 使用的重启方式
            - dry_run: 是否仅为预演
            - previous_state: 重启前服务状态
            - new_state: 重启后服务状态
            - message: 操作说明
    """
    # 模拟服务状态检查
    mock_services = {
        "data-sync-service": {"type": "docker", "status": "running", "uptime": "3h 24m"},
        "api-gateway-service": {"type": "docker", "status": "running", "uptime": "12h 5m"},
        "redis-cluster": {"type": "systemd", "status": "running", "uptime": "7d 2h"},
        "postgres-db": {"type": "systemd", "status": "running", "uptime": "30d 1h"},
    }

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "service_name": service_name,
            "method": method,
            "planned_action": f"将使用 {method} 方式重启 {service_name}",
            "warning": "dry_run 模式，未实际执行。设置 dry_run=False 确认执行。",
            "recommendation": f"建议在低峰期执行，预计影响时间 {3 if method == 'graceful' else 1 if method == 'force' else 0} 秒",
        }

    if service_name not in mock_services:
        return {
            "success": False,
            "dry_run": False,
            "service_name": service_name,
            "error": f"未找到服务 {service_name}，请确认服务名称是否正确",
        }

    svc = mock_services[service_name]
    if method == "rolling":
        return {
            "success": True,
            "dry_run": False,
            "service_name": service_name,
            "method": "rolling",
            "previous_state": svc["status"],
            "new_state": "running",
            "details": f"滚动重启完成: Pod-1 → Pod-2 → Pod-3，各间隔 30s 等待健康检查通过。服务可用性保持 100%",
        }
    elif method == "force":
        return {
            "success": True,
            "dry_run": False,
            "service_name": service_name,
            "method": "force",
            "previous_state": svc["status"],
            "new_state": "running",
            "details": f"强制重启完成: 服务已恢复运行。注意：force 重启期间有 ~2s 的请求中断",
        }
    else:  # graceful
        return {
            "success": True,
            "dry_run": False,
            "service_name": service_name,
            "method": "graceful",
            "previous_state": svc["status"],
            "new_state": "running",
            "details": f"优雅重启完成: SIGTERM → 等待 30s → 新进程启动。请求中断 < 500ms",
        }


# ============================================================
# 2. 磁盘清理
# ============================================================

@mcp.tool()
@log_tool_call
def clean_disk_space(
    target_path: str,
    clean_type: str = "logs",
    dry_run: bool = True,
    max_age_hours: int = 24,
) -> Dict[str, Any]:
    """清理磁盘空间（日志轮转、临时文件清理、Docker 镜像清理）。

    使用场景：磁盘使用率超阈值（>80%）时的应急清理、日志轮转配置失效后的手动干预。

    Args:
        target_path: 目标路径（必填）
            示例: "/var/log/app", "/tmp", "/var/lib/docker"
        clean_type: 清理类型（可选，默认 logs）
            - "logs": 清理超过 max_age_hours 的日志文件
            - "temp": 清理临时文件
            - "docker": 清理未使用的 Docker 镜像和容器
            - "cache": 清理应用缓存目录
        dry_run: 是否仅预演不实际执行（可选，默认 True）
        max_age_hours: 保留最近多少小时的日志（可选，默认 24）

    Returns:
        Dict: 执行结果
            - success: 是否成功
            - dry_run: 是否仅为预演
            - freed_space_mb: 释放的空间（MB）
            - files_removed: 删除的文件数
            - message: 操作说明
    """
    # 模拟各场景的清理结果
    mock_results = {
        ("logs", "/var/log/app"): {"files": 15, "size": 2450, "pattern": "*.log.2026-06-*"},
        ("logs", "/var/log/nginx"): {"files": 8, "size": 890, "pattern": "access.log.*"},
        ("temp", "/tmp"): {"files": 42, "size": 356, "pattern": "*.tmp, /tmp/cache/*"},
        ("docker", "/var/lib/docker"): {"files": 23, "size": 5120, "pattern": "dangling images, stopped containers"},
        ("cache", "/app/cache"): {"files": 7, "size": 150, "pattern": "*.cache"},
    }

    key = (clean_type, target_path)
    if key not in mock_results:
        return {
            "success": False,
            "dry_run": dry_run,
            "error": f"未找到匹配的清理规则: target_path={target_path}, type={clean_type}",
        }

    info = mock_results[key]
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "target_path": target_path,
            "clean_type": clean_type,
            "estimated_freed_mb": info["size"],
            "estimated_files": info["files"],
            "warning": "dry_run 模式，未实际删除。确认无误后设置 dry_run=False 执行。",
        }

    return {
        "success": True,
        "dry_run": False,
        "target_path": target_path,
        "clean_type": clean_type,
        "freed_space_mb": info["size"],
        "files_removed": info["files"],
        "details": f"已清理 {info['files']} 个文件，释放 {info['size']}MB 空间。匹配规则: {info['pattern']}",
    }


# ============================================================
# 3. 服务扩缩容
# ============================================================

@mcp.tool()
@log_tool_call
def scale_service(
    service_name: str,
    replicas: int,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """调整服务的副本数量（K8s Deployment 或 Docker Compose Scale）。

    使用场景：CPU/内存高负载时的应急扩容、流量低谷时的成本优化缩容。

    Args:
        service_name: 服务名称（必填）
        replicas: 目标副本数（必填，1-20）
        dry_run: 是否仅预演不实际执行（可选，默认 True）

    Returns:
        Dict: 执行结果
    """
    mock_state = {
        "data-sync-service": {"current": 3, "cpu_per_pod": 85.0, "memory_per_pod": 72.0},
        "api-gateway-service": {"current": 5, "cpu_per_pod": 45.0, "memory_per_pod": 38.0},
    }

    if service_name not in mock_state:
        return {"success": False, "error": f"未找到服务 {service_name}"}

    if replicas < 1 or replicas > 20:
        return {"success": False, "error": f"副本数 {replicas} 超出范围 (1-20)"}

    current = mock_state[service_name]
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "service_name": service_name,
            "current_replicas": current["current"],
            "target_replicas": replicas,
            "estimated_effect": f"预计 CPU 使用率从 {current['cpu_per_pod']}% 降至 ~{current['cpu_per_pod'] * current['current'] / replicas:.0f}%",
            "warning": "dry_run 模式，未实际执行。",
        }

    return {
        "success": True,
        "dry_run": False,
        "service_name": service_name,
        "previous_replicas": current["current"],
        "new_replicas": replicas,
        "details": f"副本数 {current['current']} → {replicas}，等待新 Pod 健康检查通过",
    }


# ============================================================
# 4. 版本回滚
# ============================================================

@mcp.tool()
@log_tool_call
def rollback_deployment(
    service_name: str,
    target_version: Optional[str] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """将服务回滚到上一个稳定版本。

    使用场景：新版本上线后出现异常（CPU 飙升、错误率上升、内存泄漏），
    需要快速回退到已知正常版本。

    自动记录回滚前状态，支持再次回滚（undo rollback）。

    Args:
        service_name: 服务名称（必填）
        target_version: 目标版本（可选）
            不填则回滚到上一个版本（kubectl rollout undo）
            填写具体版本号则回滚到指定版本
        dry_run: 是否仅预演不实际执行（可选，默认 True）

    Returns:
        Dict: 执行结果
    """
    mock_deployments = {
        "data-sync-service": {
            "current": "v2.3.1",
            "previous": "v2.3.0",
            "history": ["v2.0.0", "v2.1.0", "v2.2.0", "v2.3.0", "v2.3.1"],
            "current_status": "degraded",
            "reason": "CPU 使用率异常升高，v2.3.1 引入了未优化的批处理逻辑",
        },
        "api-gateway-service": {
            "current": "v3.1.2",
            "previous": "v3.1.1",
            "history": ["v3.0.0", "v3.1.0", "v3.1.1", "v3.1.2"],
            "current_status": "healthy",
        },
    }

    if service_name not in mock_deployments:
        return {"success": False, "error": f"未找到服务 {service_name} 的部署记录"}

    deploy = mock_deployments[service_name]
    if target_version and target_version not in deploy["history"]:
        return {"success": False, "error": f"版本 {target_version} 不在历史记录中: {deploy['history']}"}

    rollback_to = target_version or deploy["previous"]
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "service_name": service_name,
            "current_version": deploy["current"],
            "current_status": deploy["current_status"],
            "rollback_to": rollback_to,
            "available_versions": deploy["history"],
            "warning": "dry_run 模式，未实际执行。确认后设置 dry_run=False 执行回滚。",
        }

    return {
        "success": True,
        "dry_run": False,
        "service_name": service_name,
        "previous_version": deploy["current"],
        "new_version": rollback_to,
        "details": f"回滚完成: {deploy['current']} → {rollback_to}。已记录回滚前状态，可再次回滚恢复。",
    }


# ============================================================
# 5. 进程管理
# ============================================================

@mcp.tool()
@log_tool_call
def kill_process(
    process_pattern: str,
    signal: str = "SIGTERM",
    dry_run: bool = True,
) -> Dict[str, Any]:
    """终止匹配指定模式的进程。

    使用场景：僵尸进程占用资源、失控的批处理任务、内存泄漏的 Java 进程需要强制终止。
    SIGTERM(15) 是优雅终止（进程可自行清理），SIGKILL(9) 是强制杀死（不留清理机会）。

    Args:
        process_pattern: 进程匹配模式（必填）
            示例: "batch-sync", "java.*DataSync", "python.*train"
        signal: 信号类型（可选，默认 SIGTERM）
            - "SIGTERM": 优雅终止（给进程清理资源的机会）
            - "SIGKILL": 强制杀死（进程无法响应 SIGTERM 时使用）
        dry_run: 是否仅预演（可选，默认 True）

    Returns:
        Dict: 执行结果
    """
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "process_pattern": process_pattern,
            "signal": signal,
            "matched_count": 2,
            "matched_pids": [28473, 28475],
            "warning": f"dry_run 模式，未实际执行。将使用 {signal} 终止匹配 '{process_pattern}' 的 {2} 个进程。",
        }

    return {
        "success": True,
        "dry_run": False,
        "process_pattern": process_pattern,
        "signal": signal,
        "killed_count": 2,
        "killed_pids": [28473, 28475],
        "details": f"已用 {signal} 终止 2 个进程 (PIDs: 28473, 28475)",
    }


# ============================================================
# 6. 配置热更新
# ============================================================

@mcp.tool()
@log_tool_call
def update_config(
    config_path: str,
    key: str,
    value: str,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """在线更新服务配置参数（无需重启）。

    使用场景：告警阈值调整（CPU 80%→90%）、连接池大小调整、日志级别动态调整、
    限流参数调整、缓存过期时间修改。

    支持自动备份原配置，更新失败时自动恢复。

    Args:
        config_path: 配置文件路径（必填）
            示例: "/etc/app/config.yaml", "env://DATA_SYNC_POOL_SIZE"
        key: 配置项名称（必填）
            示例: "alert.cpu_threshold", "db.pool.max_size", "logging.level"
        value: 新值（必填）
            示例: "90", "200", "DEBUG"
        dry_run: 是否仅预演（可选，默认 True）

    Returns:
        Dict: 执行结果
            - success: 是否成功
            - previous_value: 修改前的值（用于回滚）
            - backup_path: 配置备份路径
    """
    mock_configs = {
        ("/etc/app/config.yaml", "alert.cpu_threshold"): {"current": "80", "valid_range": "50-100"},
        ("/etc/app/config.yaml", "db.pool.max_size"): {"current": "100", "valid_range": "10-500"},
        ("/etc/app/config.yaml", "logging.level"): {"current": "INFO", "valid_values": ["DEBUG", "INFO", "WARN", "ERROR"]},
        ("env://DATA_SYNC_POOL_SIZE", "pool_size"): {"current": "100", "valid_range": "10-500"},
    }

    key_tuple = (config_path, key)
    if key_tuple not in mock_configs:
        return {"success": False, "error": f"未找到配置项: {config_path} -> {key}"}

    cfg = mock_configs[key_tuple]
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "config_path": config_path,
            "key": key,
            "current_value": cfg["current"],
            "new_value": str(value),
            "backup_path": f"{config_path}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "warning": "dry_run 模式，未实际修改。确认后设置 dry_run=False 执行。",
        }

    return {
        "success": True,
        "dry_run": False,
        "config_path": config_path,
        "key": key,
        "previous_value": cfg["current"],
        "new_value": str(value),
        "backup_path": f"{config_path}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8005, path="/mcp")
