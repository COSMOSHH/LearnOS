from pathlib import Path


ZERO_RAG_DIR = Path(__file__).resolve().parent.parent


md5_path = str(ZERO_RAG_DIR / "processed_md5.json")
chroma_name = "study-agent-rag"
persist_directory = str(ZERO_RAG_DIR / "chroma_db")

chunk_size = 400
chunk_overlap = 50
separators = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]
max_split_char_num = 400

similarity_threshold = 20
rerank_model = "gte-rerank"
rerank_top_k = 3

embedding_model = "text-embedding-v4"
chat_model = "qwen3-max"
