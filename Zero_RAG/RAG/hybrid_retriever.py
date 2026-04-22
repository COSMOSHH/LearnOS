from typing import Dict, List
import warnings

import dashscope
import jieba
from rank_bm25 import BM25Okapi

from . import config

warnings.filterwarnings("ignore", category=UserWarning, module="jieba")


class HybridRetriever:
    def __init__(
        self,
        vector_store,
        doc_chunks: List[Dict],
        vector_top_k=5,
        bm25_top_k=5,
        final_top_k=3,
        vector_where: dict | None = None,
    ):
        self.vector_store = vector_store
        self.doc_chunks = doc_chunks
        self.vector_top_k = vector_top_k
        self.bm25_top_k = bm25_top_k
        self.final_top_k = final_top_k
        self.vector_where = vector_where

        tokenized_corpus = [list(jieba.cut(chunk["chunk_text"])) for chunk in self.doc_chunks] if self.doc_chunks else [[]]
        self.bm25 = BM25Okapi(tokenized_corpus)

    def retrieve(self, query: str) -> List[Dict]:
        vector_results = self.vector_store.similarity_search(
            query,
            top_k=self.vector_top_k,
            where=self.vector_where,
        )
        vector_docs = {res["document"]: res["metadata"] for res in vector_results}

        tokenized_query = list(jieba.cut(query))
        bm25_scores = self.bm25.get_scores(tokenized_query) if self.doc_chunks else []
        bm25_top_n_idx = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[: self.bm25_top_k]

        bm25_docs = {}
        for index in bm25_top_n_idx:
            chunk = self.doc_chunks[index]
            bm25_docs[chunk["chunk_text"]] = chunk["metadata"]

        merged_docs = {**vector_docs, **bm25_docs}
        unique_texts = list(merged_docs.keys())
        if not unique_texts:
            return []

        try:
            response = dashscope.TextReRank.call(
                model=config.rerank_model,
                query=query,
                documents=unique_texts,
                top_n=self.final_top_k,
                return_documents=False,
            )
            if response.status_code == 200:
                final_results = []
                for item in response.output.results:
                    index = item.index
                    final_results.append(
                        {
                            "document": unique_texts[index],
                            "metadata": merged_docs[unique_texts[index]],
                            "score": item.relevance_score,
                        }
                    )
                return final_results
        except Exception:
            pass

        return [
            {"document": text, "metadata": merged_docs[text], "score": 0.0}
            for text in unique_texts[: self.final_top_k]
        ]
