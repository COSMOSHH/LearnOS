import chromadb

from . import config
from .embeddings import TongyiEmbeddings


class ChromaDBStore:
    def __init__(self, collection_name=config.chroma_name, persist_dir=config.persist_directory):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embeddings_model = TongyiEmbeddings()
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def add_documents(self, documents: list[str], metadatas: list[dict], ids: list[str], progress_callback=None):
        batch_size = 10

        for index in range(0, len(documents), batch_size):
            batch_docs = documents[index:index + batch_size]
            batch_metas = metadatas[index:index + batch_size] if metadatas else None
            batch_ids = ids[index:index + batch_size] if ids else None
            embeddings = self.embeddings_model.embed_documents(batch_docs)
            self.collection.add(
                documents=batch_docs,
                embeddings=embeddings,
                metadatas=batch_metas,
                ids=batch_ids,
            )
            if progress_callback:
                progress_callback(min(index + len(batch_docs), len(documents)), len(documents))

    def similarity_search(self, query: str, top_k=5, where: dict | None = None) -> list[dict]:
        query_embedding = self.embeddings_model.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )

        search_results = []
        if results["documents"] and len(results["documents"]) > 0:
            for index in range(len(results["documents"][0])):
                search_results.append(
                    {
                        "document": results["documents"][0][index],
                        "metadata": results["metadatas"][0][index] if results["metadatas"] else None,
                        "distance": results["distances"][0][index] if "distances" in results else None,
                    }
                )
        return search_results

    def get_all_documents(self, where: dict | None = None) -> list[dict]:
        results = self.collection.get(where=where)
        all_chunks = []
        if results and "documents" in results and results["documents"]:
            for document, metadata in zip(results["documents"], results["metadatas"]):
                all_chunks.append({"chunk_text": document, "metadata": metadata if metadata else {}})
        return all_chunks

    def delete_documents(self, where: dict | None = None, ids: list[str] | None = None):
        kwargs = {}
        if where:
            kwargs["where"] = where
        if ids:
            kwargs["ids"] = ids
        if kwargs:
            self.collection.delete(**kwargs)
