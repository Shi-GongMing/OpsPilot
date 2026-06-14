"""
共享的 Checkpoint 管理器
使用 AsyncSqliteSaver 实现会话跨重启持久化
"""

import aiosqlite
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from loguru import logger

DB_PATH = Path("data/checkpoints.db")

# 全局单例
_sqlite_saver: AsyncSqliteSaver | None = None
_conn: aiosqlite.Connection | None = None


async def get_checkpointer() -> AsyncSqliteSaver:
    """获取全局 AsyncSqliteSaver 实例（延迟初始化，需在 async 上下文中调用）"""
    global _sqlite_saver, _conn
    if _sqlite_saver is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = await aiosqlite.connect(str(DB_PATH.absolute()))
        _sqlite_saver = AsyncSqliteSaver(_conn)
        await _sqlite_saver.setup()
        logger.info(f"AsyncSqliteSaver 初始化完成: {DB_PATH.absolute()}")
    return _sqlite_saver


async def close_checkpointer():
    """关闭数据库连接（应用关闭时调用）"""
    global _sqlite_saver, _conn
    if _conn:
        await _conn.close()
        _conn = None
        _sqlite_saver = None
        logger.info("AsyncSqliteSaver 连接已关闭")


def get_checkpointer_db_path() -> str:
    """获取 checkpointer 数据库文件路径"""
    return str(DB_PATH.absolute())
