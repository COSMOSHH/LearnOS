from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
ZERO_RAG_DIR = Path(__file__).resolve().parent


# Vector store
chroma_name = "study-agent-rag"
persist_directory = str(ZERO_RAG_DIR / "chroma_db")


# Uploads
upload_directory = str(BASE_DIR / "uploaded_study_materials")


# Text splitting
chunk_size = 400
chunk_overlap = 50
separators = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]
max_split_char_num = 400


# Retrieval
similarity_threshold = 20
rerank_model = "gte-rerank"
rerank_top_k = 3


# Models
embedding_model = "text-embedding-v4"
chat_model = "qwen-plus"


# Database / knowledge defaults
knowledge_base_dir = str(BASE_DIR / "study_knowledge_base")
