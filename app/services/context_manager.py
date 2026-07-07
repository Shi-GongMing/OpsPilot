"""
三层上下文管理 Middleware

策略：
  Layer 1 - 全量窗口: 最近 K_FULL 条消息完整保留，保证当前话题连贯性
  Layer 2 - 摘要压缩: 溢出消息经 LLM 异步压缩为结构化摘要，注入当前对话
  Layer 3 - 向量归档: 所有摘要按 session_id 写入 Milvus，支持跨会话语义检索

基于 LangChain AgentMiddleware 子类化实现，在每次 model 调用前触发。
"""

from datetime import datetime
from typing import Any

from langchain.agents.factory import AgentMiddleware
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.services.document_splitter_service import document_splitter_service
from app.services.vector_store_manager import vector_store_manager

# 全量保留最近 10 条（5 轮对话）
K_FULL_WINDOW = 10
# 摘要压缩触发阈值
K_COMPRESS_TRIGGER = 16
# 每次压缩的消息数
M_COMPRESS_BATCH = 8

# 摘要消息标记
SUMMARY_ROLE = "_context_summary"


def _build_summary_prompt(messages: list[BaseMessage]) -> str:
    conversation = []
    for m in messages:
        role = type(m).__name__.replace("Message", "").lower()
        content = str(m.content)[:400]
        conversation.append(f"[{role}]: {content}")
    return (
        "请将以下对话历史压缩为简短结构化摘要，保留关键信息：\n\n"
        + "\n".join(conversation)
        + "\n\n要求：保留关键决策、工具调用结果中的核心数据、用户明确表达的偏好或需求。不超过 200 字，中文。"
    )


def _is_summary(msg: BaseMessage) -> bool:
    if isinstance(msg, HumanMessage):
        return (msg.additional_kwargs or {}).get("role") == SUMMARY_ROLE
    return False


def _dedupe_summaries(messages: list[BaseMessage]) -> list[BaseMessage]:
    """只保留最近一条摘要"""
    cleaned: list[BaseMessage] = []
    found = False
    for msg in reversed(messages):
        if _is_summary(msg):
            if found:
                continue
            found = True
            cleaned.insert(0, msg)
        else:
            cleaned.insert(0, msg)
    return cleaned


def _filter_non_display(messages: list[BaseMessage]) -> list[BaseMessage]:
    """过滤掉摘要消息（用于传给 LLM 时减少冗余）"""
    return [m for m in messages if not _is_summary(m)]


class ContextWindowMiddleware(AgentMiddleware):
    """三层上下文窗口管理 Middleware"""

    async def abefore_model(
        self, state: dict[str, Any], runtime: RunnableConfig
    ) -> dict[str, Any] | None:
        messages: list[BaseMessage] = list(state.get("messages", []))
        messages = _dedupe_summaries(messages)

        if len(messages) <= K_COMPRESS_TRIGGER:
            return None

        # 分离保留和压缩
        preserve = messages[-K_FULL_WINDOW:]  # 最近 10 条完整保留
        to_compress_all = messages[: len(messages) - K_FULL_WINDOW]
        compress_batch = to_compress_all[-M_COMPRESS_BATCH:]

        # 异步 LLM 压缩
        summary_text = await self._compress(compress_batch)

        summary_msg = HumanMessage(
            content=f"[历史对话摘要]\n{summary_text}",
            additional_kwargs={"role": SUMMARY_ROLE},
        )
        logger.info(
            f"[上下文] 压缩 {len(compress_batch)} 条 → "
            f"{len(summary_text)} 字摘要, 保留 {len(preserve)} 条"
        )

        # 后台归档
        self._schedule_archive(summary_text, runtime)

        return {"messages": [summary_msg] + preserve}

    async def _compress(self, messages: list[BaseMessage]) -> str:
        try:
            from app.config import config
            from langchain_qwq import ChatQwen

            llm = ChatQwen(
                model=config.rag_model,
                api_key=config.dashscope_api_key,
                temperature=0.3,
                streaming=False,
                enable_thinking=False,
            )
            prompt = _build_summary_prompt(messages)
            response = await llm.ainvoke(prompt)
            return str(response.content) if hasattr(response, 'content') else str(response)
        except Exception as e:
            logger.warning(f"[上下文] 摘要压缩失败: {e}")
            return f"[压缩失败] {len(messages)} 条历史消息已丢弃"

    @staticmethod
    def _schedule_archive(summary_text: str, runtime: RunnableConfig):
        import asyncio

        cfg = runtime.get("configurable", {}) if isinstance(runtime, dict) else {}
        # 兼容 agent 可能将 config 放在 metadata 中的情况
        session_id = cfg.get("thread_id") or cfg.get("session_id") or "unknown"

        def _sync_archive():
            try:
                content = (
                    f"# 对话摘要归档\n"
                    f"- 归档时间: {datetime.now().isoformat()}\n"
                    f"- 会话 ID: {session_id}\n\n"
                    f"{summary_text}"
                )
                chunks = document_splitter_service.split_text(
                    content, f"context_summary/{session_id}.txt"
                )
                if not chunks:
                    return
                for chunk in chunks:
                    chunk.metadata["_source_type"] = "context_summary"
                    chunk.metadata["_summary_time"] = datetime.now().isoformat()
                ids = vector_store_manager.add_documents(chunks)
                logger.info(f"[上下文归档] {session_id}: {len(ids)} chunks")
            except Exception as e:
                logger.warning(f"[上下文归档] 失败: {e}")

        try:
            asyncio.ensure_future(asyncio.to_thread(_sync_archive))
        except Exception as e:
            logger.warning(f"[上下文归档] 调度失败: {e}")
