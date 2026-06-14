"""向量嵌入服务模块 - 基于 LangChain Embeddings 标准接口"""

from typing import List

from langchain_core.embeddings import Embeddings
from openai import OpenAI
from loguru import logger

from app.config import config


class DashScopeEmbeddings(Embeddings):
    """阿里云 DashScope Text Embedding (OpenAI 兼容模式)
    
    实现 LangChain 标准 Embeddings 接口:
    - embed_documents(texts: List[str]) → List[List[float]]: 批量嵌入文档
    - embed_query(text: str) → List[float]: 嵌入单个查询
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
    ):
        self.model = model
        self.dimensions = dimensions
        self.client = None

        if not api_key or api_key == "your-api-key-here":
            logger.warning(
                "DashScope API Key 未配置，Embedding 服务将不可用。"
                "请在 .env 中设置 DASHSCOPE_API_KEY。"
            )
            return

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        logger.info(
            f"DashScope Embeddings 初始化完成 - "
            f"模型: {model}, 维度: {dimensions}, API Key: {self._mask_api_key(api_key)}"
        )

    def _ensure_client(self):
        if self.client is None:
            raise RuntimeError(
                "Embedding 服务不可用：DASHSCOPE_API_KEY 未配置。"
                "请在 .env 文件中设置有效的 API Key。"
            )

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        if len(api_key) > 8:
            return f"{api_key[:8]}...{api_key[-4:]}"
        return "***"

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        self._ensure_client()
        try:
            logger.info(f"批量嵌入 {len(texts)} 个文档")
            
            # 批量调用 API
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
                dimensions=self.dimensions,
                encoding_format="float"
            )
            
            embeddings = [item.embedding for item in response.data]
            logger.debug(f"批量嵌入完成, 维度: {len(embeddings[0])}")
            
            return embeddings
            
        except Exception as e:
            logger.error(f"批量嵌入失败: {e}")
            raise RuntimeError(f"批量嵌入失败: {e}") from e

    def embed_query(self, text: str) -> List[float]:
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")
        self._ensure_client()
        try:
            logger.debug(f"嵌入查询, 长度: {len(text)} 字符")
            
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
                encoding_format="float"
            )
            
            embedding = response.data[0].embedding
            logger.debug(f"查询嵌入完成, 维度: {len(embedding)}")
            
            return embedding
            
        except Exception as e:
            logger.error(f"查询嵌入失败: {e}")
            raise RuntimeError(f"查询嵌入失败: {e}") from e


# 全局单例（延迟初始化，避免未配置 API Key 时阻塞应用启动）
_vector_embedding_service: DashScopeEmbeddings | None = None


def get_embedding_service() -> DashScopeEmbeddings:
    global _vector_embedding_service
    if _vector_embedding_service is None:
        _vector_embedding_service = DashScopeEmbeddings(
            api_key=config.dashscope_api_key,
            model=config.dashscope_embedding_model,
            dimensions=1024,
        )
    return _vector_embedding_service


# 保持向后兼容的模块级访问
def __getattr__(name: str):
    if name == "vector_embedding_service":
        return get_embedding_service()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
