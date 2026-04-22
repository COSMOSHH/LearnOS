import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="jieba")
import os
import json
import hashlib
from RAG.document_loader import DirectoryLoader
from RAG.text_splitter import SemanticTextSplitter
from RAG.vector_store import ChromaDBStore
import config




RECORD_FILE = "processed_md5.json"


def load_processed_md5() -> set:
    """加载已处理的文件 MD5 记录"""
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_processed_md5(md5_set: set):
    """保存已处理的文件 MD5 记录"""
    with open(RECORD_FILE, "w", encoding="utf-8") as f:
        json.dump(list(md5_set), f, ensure_ascii=False, indent=2)


def calculate_md5(text: str) -> str:
    """计算文本内容的 MD5 值"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def calculate_hash(text: str) -> str:
    """计算文本内容的 SHA-256 值"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    # 1. 批量解析文件夹内所有文档
    folder_path = "data"
    dir_loader = DirectoryLoader(folder_path)
    loaded_docs = dir_loader.load()

    # 读取查重记录
    processed_md5_set = load_processed_md5()
    new_docs = []
    new_md5_set = set()

    # MD5 查重过滤
    for doc in loaded_docs:
        # 基于提取出的文本内容计算 MD5
        doc_md5 = calculate_md5(doc["text"])

        if doc_md5 not in processed_md5_set:
            new_docs.append(doc)
            new_md5_set.add(doc_md5)

    if not new_docs:
        print("没有发现新内容或变更的文件，所有文件均已入库。")
        exit()

    print(f"共发现 {len(loaded_docs)} 个文件，本次将新增处理 {len(new_docs)} 个新文件或已修改文件")

    # 2. 初始化文本切分器
    splitter = SemanticTextSplitter(chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap)
    all_chunks = []

    # 3. 遍历每个新文档进行切分，保留元数据
    for doc in new_docs:
        chunks = splitter.split_text(doc["text"])
        for chunk in chunks:
            all_chunks.append({
                "chunk_text": chunk,
                "metadata": doc["metadata"]
            })

    print(f"总计切分出 {len(all_chunks)} 个片段")

    # 4. 索引构建与入库
    if all_chunks:
        print("\n开始将文本转化为向量并写入 Chroma 数据库...")
        documents = [item["chunk_text"] for item in all_chunks]
        metadatas = [item["metadata"] for item in all_chunks]
        ids = [f"doc_{metadatas[i]['source']}_{i}" for i in range(len(all_chunks))]

        store = ChromaDBStore()
        store.add_documents(documents=documents, metadatas=metadatas, ids=ids)

        # 更新并保存处理记录
        processed_md5_set.update(new_md5_set)
        save_processed_md5(processed_md5_set)

        print("新增向量索引构建并入库成功！")

        # # 5. 简单检索测试
        # test_query = "中南林业科技大学"
        # print(f"\n--- 执行检索测试: '{test_query}' ---")
        # search_results = store.similarity_search(test_query, top_k=2)
        #
        # for idx, result in enumerate(search_results):
        #     print(f"\n[检索结果 {idx + 1}]")
        #     print(f"距离 (Distance): {result['distance']}")
        #     print(f"来源 (Source): {result['metadata']['source']}")
        #     print(f"内容预览: {result['document'][:100]}...")
