"""知识检索工具 - 从向量数据库中检索相关信息

支持 L2 距离阈值过滤 + 时间衰减加权，减少低质量噪声并倾向近期数据。
"""

from datetime import datetime, timezone
from typing import List, Tuple

from langchain_core.documents import Document
from langchain_core.tools import tool
from loguru import logger

from app.config import config
from app.services.vector_store_manager import vector_store_manager

# L2 距离阈值：仅保留 L2 < 此值的文档（text-embedding-v4 实测：强匹配 0.5-0.8，噪声 > 1.0）
L2_DISTANCE_THRESHOLD = 1.0

# 时间衰减系数 λ：文档年龄每增加 1 天，权重乘以 e^(-λ)
# ln(2)/30 ≈ 0.023 → 30 天前文档权重降为一半
TIME_DECAY_LAMBDA = 0.023


def _l2_to_similarity(l2_distance: float) -> float:
    """将 L2 距离转换为 [0, 1] 区间的相似度分数"""
    return 1.0 / (1.0 + l2_distance)


def _parse_doc_timestamp(metadata: dict) -> datetime | None:
    """从文档元数据中提取时间戳"""
    raw = metadata.get("_diagnosis_time") or metadata.get("_indexed_at")
    if not raw:
        return None
    try:
        # 支持 ISO 格式字符串
        return datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None


def _compute_time_weight(doc_timestamp: datetime | None) -> float:
    """计算时间衰减权重 e^(-λ · Δt)，无时间戳的文档权重为 1.0"""
    if doc_timestamp is None:
        return 1.0
    now = datetime.now(timezone.utc)
    if doc_timestamp.tzinfo is None:
        doc_timestamp = doc_timestamp.replace(tzinfo=timezone.utc)
    delta_days = max(0.0, (now - doc_timestamp).total_seconds() / 86400.0)
    import math
    return math.exp(-TIME_DECAY_LAMBDA * delta_days)


@tool(response_format="content_and_artifact")
def retrieve_knowledge(query: str) -> Tuple[str, List[Document]]:
    """从知识库中检索相关信息来回答问题

    当用户的问题涉及专业知识、文档内容或需要参考资料时，使用此工具。
    结果经过 L2 距离阈值过滤和时间衰减加权排序。

    Args:
        query: 用户的问题或查询

    Returns:
        Tuple[str, List[Document]]: (格式化的上下文文本, 原始文档列表)
    """
    try:
        logger.info(f"知识检索工具被调用: query='{query}'")

        vector_store = vector_store_manager.get_vector_store()

        # 拉取多于 k 的候选（留足过滤余量）
        fetch_k = max(config.rag_top_k * 3, 9)
        docs_with_scores = vector_store.similarity_search_with_score(query, k=fetch_k)

        if not docs_with_scores:
            logger.warning("未检索到相关文档")
            return "没有找到相关信息。", []

        # 过滤 + 重排序
        scored: list[tuple[float, Document]] = []
        filtered_count = 0

        for doc, l2_distance in docs_with_scores:
            if l2_distance > L2_DISTANCE_THRESHOLD:
                filtered_count += 1
                continue

            similarity = _l2_to_similarity(l2_distance)
            ts = _parse_doc_timestamp(doc.metadata)
            time_weight = _compute_time_weight(ts)
            combined = similarity * time_weight

            scored.append((combined, doc))

        scored.sort(key=lambda x: x[0], reverse=True)

        # 截取 top_k
        top_k = min(config.rag_top_k, len(scored))
        top_docs = [doc for _, doc in scored[:top_k]]

        if not top_docs:
            logger.warning(
                f"L2 阈值 {L2_DISTANCE_THRESHOLD} 过滤掉了全部 {len(docs_with_scores)} 条结果"
            )
            return "没有找到相关信息。", []

        logger.info(
            f"检索: 候选 {len(docs_with_scores)} → "
            f"过滤 {filtered_count} → 时间衰减排序 → 返回 {len(top_docs)}"
        )

        context = format_docs(top_docs)
        return context, top_docs

    except Exception as e:
        logger.error(f"知识检索工具调用失败: {e}")
        return f"检索知识时发生错误: {str(e)}", []


def format_docs(docs: List[Document]) -> str:
    """格式化文档列表为上下文文本"""
    formatted_parts = []

    for i, doc in enumerate(docs, 1):
        metadata = doc.metadata
        source = metadata.get("_file_name", "未知来源")
        headers = []
        for key in ["h1", "h2", "h3"]:
            if key in metadata and metadata[key]:
                headers.append(metadata[key])
        header_str = " > ".join(headers) if headers else ""

        formatted = f"【参考资料 {i}】"
        if header_str:
            formatted += f"\n标题: {header_str}"
        formatted += f"\n来源: {source}"

        # 显示归档时间
        diag_time = metadata.get("_diagnosis_time", "")
        if diag_time:
            formatted += f"\n归档时间: {diag_time}"

        formatted += f"\n内容:\n{doc.page_content}\n"
        formatted_parts.append(formatted)

    return "\n".join(formatted_parts)
