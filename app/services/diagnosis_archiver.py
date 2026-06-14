"""
诊断结果自动归档服务

诊断完成后，将故障处理经验自动生成标准文档并写入 Milvus 向量数据库，
形成"诊断 → 归档 → 检索增强 → 更优诊断"的自演进闭环。
"""

from datetime import datetime
from pathlib import Path

from langchain_core.documents import Document
from loguru import logger

from app.config import config
from app.services.document_splitter_service import document_splitter_service
from app.services.vector_store_manager import vector_store_manager

# 归档文件名前缀
ARCHIVE_PREFIX = "auto_archive"


def build_archive_document(
    session_id: str,
    user_input: str,
    past_steps: list[tuple],
    final_response: str,
) -> str:
    """
    将诊断过程结构化拼接为标准故障报告文档

    Args:
        session_id: 诊断会话 ID
        user_input: 原始诊断任务
        past_steps: 已执行步骤 [(步骤描述, 执行结果), ...]
        final_response: 最终诊断报告

    Returns:
        完整的 Markdown 文档
    """
    now_iso = datetime.now().isoformat()
    lines = [
        f"# 自动归档故障案例",
        f"",
        f"- **归档时间**: {now_iso}",
        f"- **会话 ID**: {session_id}",
        f"- **来源**: AIOps 自动诊断归档",
        f"",
        f"---",
        f"",
        f"## 诊断任务",
        f"",
        f"{user_input}",
        f"",
        f"---",
        f"",
        f"## 排查过程",
        f"",
    ]

    for i, (step, result) in enumerate(past_steps, 1):
        lines.append(f"### 步骤 {i}: {step}")
        lines.append(f"")
        # 控制每个步骤结果长度，保留关键信息
        truncated = result if len(result) <= 2000 else result[:2000] + "\n\n...(结果过长已截断)"
        lines.append(f"{truncated}")
        lines.append(f"")

    lines.extend([
        "---",
        "",
        "## 诊断结论",
        "",
        final_response,
    ])

    return "\n".join(lines)


def write_archive_to_vectorstore(session_id: str, doc_content: str):
    """
    将归档文档分割并写入 Milvus 向量库

    Args:
        session_id: 诊断会话 ID（用于生成虚拟文件名）
        doc_content: 完整的 Markdown 文档内容
    """
    archive_file_name = f"{ARCHIVE_PREFIX}/{session_id}.md"

    try:
        # 分割文档
        chunks = document_splitter_service.split_markdown(doc_content, archive_file_name)
        if not chunks:
            logger.warning(f"[归档] 文档分割为空: {session_id}")
            return

        # 添加归档标记元数据
        for chunk in chunks:
            chunk.metadata["_source_type"] = "auto_archive"
            chunk.metadata["_diagnosis_time"] = datetime.now().isoformat()

        # 删除同 session 的旧归档（支持覆盖更新）
        deleted = vector_store_manager.delete_by_source(archive_file_name)
        if deleted > 0:
            logger.info(f"[归档] 已删除 {session_id} 的旧归档 ({deleted} 条)")

        # 写入 Milvus
        ids = vector_store_manager.add_documents(chunks)
        logger.info(f"[归档] 成功归档 {session_id}: {len(ids)} 个分片")

    except Exception as e:
        logger.error(f"[归档] 写入向量库失败 {session_id}: {e}")


def archive_diagnosis(
    session_id: str,
    user_input: str,
    past_steps: list[tuple],
    final_response: str,
):
    """
    执行诊断归档的主入口

    Args:
        session_id: 诊断会话 ID
        user_input: 原始诊断任务
        past_steps: 已执行步骤列表
        final_response: 最终诊断报告
    """
    if not final_response or len(final_response.strip()) < 50:
        logger.info(f"[归档] 诊断报告内容过短，跳过归档: {session_id}")
        return

    logger.info(f"[归档] 开始归档诊断结果: {session_id}")

    try:
        doc_content = build_archive_document(
            session_id=session_id,
            user_input=user_input,
            past_steps=past_steps,
            final_response=final_response,
        )

        write_archive_to_vectorstore(
            session_id=session_id,
            doc_content=doc_content,
        )

        logger.info(f"[归档] 归档完成: {session_id}, 文档长度: {len(doc_content)} 字符")

    except Exception as e:
        logger.error(f"[归档] 归档失败 {session_id}: {e}")
