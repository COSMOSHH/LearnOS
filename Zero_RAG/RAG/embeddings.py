import os
import dashscope
from dashscope import TextEmbedding
from . import config

class TongyiEmbeddings:
    def __init__(self, model_name=config.embedding_model):
        self.model_name = model_name
        dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """为多个文档生成向量"""
        try:
            response = TextEmbedding.call(
                model=self.model_name,
                input=texts
            )
            if response.status_code == 200:
                # 返回向量列表，保持与输入文本顺序一致
                return [item['embedding'] for item in response.output['embeddings']]
            else:
                raise Exception(f"Embedding 失败: {response.code} - {response.message}")
        except Exception as e:
            raise Exception(f"发生异常: {str(e)}")

    def embed_query(self, text: str) -> list[float]:
        """为单个查询生成向量"""
        return self.embed_documents([text])[0]