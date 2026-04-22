import warnings
import os

# 1. 屏蔽所有底层触发的 pkg_resources 弃用警告和 jieba 警告
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")
warnings.filterwarnings("ignore", category=UserWarning, module="jieba")

# 2. 屏蔽 Hugging Face 在 Windows 下的符号链接警告
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from RAG.vector_store import ChromaDBStore
from RAG.hybrid_retriever import HybridRetriever
from llm_generator import LLMGenerator
import config


def main():
    # 1. 设定用户提问
    query = "胡宇的绩点是多少？"
    print(f"用户提问: {query}\n")

    # 2. 初始化向量数据库 (假设在 ingest_data.py 中已构建好索引)
    print("正在连接 Chroma 数据库...")
    vector_store = ChromaDBStore()

    # 模拟从数据库或本地加载全量文档片段和元数据（混合检索的 BM25 需要全文）
    # 实际应用中可以从 Chroma 数据库读取，或在启动时加载预处理好的本地 JSON 数据
    # 此处假设提供一个获取所有文档的接口或直接读取之前生成的片段
    all_chunks = vector_store.get_all_documents()  # 需确保 ChromaDBStore 有此方法，或自行加载

    if not all_chunks:
        print("未检测到文档数据，请先运行 ingest_data.py 进行数据入库。")
        return

    # 3. 初始化混合检索器与重排序模型
    print("正在初始化混合检索器 (BM25 + 向量 + Reranker)...")
    retriever = HybridRetriever(
        vector_store=vector_store,
        doc_chunks=all_chunks,
        vector_top_k=5,
        bm25_top_k=5,
        final_top_k=3
    )

    # 4. 执行混合检索
    print("正在检索相关文档...")
    retrieved_results = retriever.retrieve(query)

    print(f"共召回并精选出 {len(retrieved_results)} 个相关片段：")
    for idx, res in enumerate(retrieved_results):
        print(f"  [{idx + 1}] 得分: {res['score']:.4f} | 来源: {res['metadata'].get('source', '未知')}")

    # 5. 初始化 LLM 生成器并生成答案
    print("\n正在调用大模型生成回答...")
    # 请确保已设置 OPENAI_API_KEY 环境变量，或在下方直接传入 api_key="sk-..."
    llm_generator = LLMGenerator(
        model_name=config.chat_model,
        api_key = os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    )

    final_answer = llm_generator.generate_answer(query, retrieved_results)

    # 6. 输出最终结果
    print("\n" + "=" * 40)
    print("AI 回答:")
    print("=" * 40)
    print(final_answer)


if __name__ == "__main__":
    main()
