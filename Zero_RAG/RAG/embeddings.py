import os
import time
import dashscope
from dashscope import TextEmbedding
from . import config

class TongyiEmbeddings:
    def __init__(self, model_name=config.embedding_model, max_retries: int | None = None, retry_base_seconds: float | None = None):
        self.model_name = model_name
        dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.max_retries = max_retries if max_retries is not None else int(os.getenv("EMBEDDING_MAX_RETRIES", "5"))
        self.retry_base_seconds = (
            retry_base_seconds
            if retry_base_seconds is not None
            else float(os.getenv("EMBEDDING_RETRY_BASE_SECONDS", "2"))
        )

    def _is_non_retryable_error(self, message: str) -> bool:
        normalized = (message or "").lower()
        non_retryable_markers = [
            "access denied",
            "overdue-payment",
            "invalid api-key",
            "invalidapikey",
            "unauthorized",
            "forbidden",
        ]
        return any(marker in normalized for marker in non_retryable_markers)

    def _sleep_before_retry(self, attempt: int):
        delay = min(60.0, self.retry_base_seconds * (2 ** max(0, attempt - 1)))
        time.sleep(delay)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """为多个文档生成向量"""
        last_error = None
        for attempt in range(1, self.max_retries + 2):
            try:
                response = TextEmbedding.call(
                    model=self.model_name,
                    input=texts
                )
                if response.status_code == 200:
                    # 返回向量列表，保持与输入文本顺序一致
                    return [item['embedding'] for item in response.output['embeddings']]

                error_message = f"Embedding 失败: {response.code} - {response.message}"
                if self._is_non_retryable_error(error_message):
                    raise Exception(error_message)
                last_error = error_message
            except Exception as e:
                error_message = str(e)
                if self._is_non_retryable_error(error_message):
                    raise Exception(f"发生异常: {error_message}")
                last_error = error_message

            if attempt <= self.max_retries:
                self._sleep_before_retry(attempt)

        raise Exception(f"发生异常: Embedding 重试 {self.max_retries} 次后仍失败: {last_error}")

    def embed_query(self, text: str) -> list[float]:
        """为单个查询生成向量"""
        return self.embed_documents([text])[0]
