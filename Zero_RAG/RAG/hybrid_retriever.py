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
        results, _ = self.retrieve_with_debug(query)
        return results

    def retrieve_with_debug(self, query: str) -> tuple[List[Dict], Dict]:
        vector_results = self.vector_store.similarity_search(
            query,
            top_k=self.vector_top_k,
            where=self.vector_where,
        )
        debug_payload = {
            "query": query,
            "vector_top_k": self.vector_top_k,
            "bm25_top_k": self.bm25_top_k,
            "final_top_k": self.final_top_k,
            "vector_candidates": [],
            "bm25_candidates": [],
            "reranked_results": [],
        }

        merged_candidates = {}
        for result in vector_results:
            metadata = result.get("metadata") or {}
            candidate_key = self._build_candidate_key(result["document"], metadata)
            merged_candidates[candidate_key] = {
                "document": result["document"],
                "metadata": metadata,
                "vector_distance": result.get("distance"),
                "bm25_score": None,
            }
            debug_payload["vector_candidates"].append(
                {
                    "document_title": metadata.get("document_title", ""),
                    "section_title": metadata.get("section_title", ""),
                    "chunk_index": metadata.get("chunk_index"),
                    "distance": result.get("distance"),
                }
            )

        tokenized_query = list(jieba.cut(query))
        bm25_scores = self.bm25.get_scores(tokenized_query) if self.doc_chunks else []
        bm25_top_n_idx = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[: self.bm25_top_k]

        for index in bm25_top_n_idx:
            chunk = self.doc_chunks[index]
            metadata = chunk.get("metadata") or {}
            candidate_key = self._build_candidate_key(chunk["chunk_text"], metadata)
            merged_candidates[candidate_key] = {
                "document": chunk["chunk_text"],
                "metadata": metadata,
                "vector_distance": merged_candidates.get(candidate_key, {}).get("vector_distance"),
                "bm25_score": bm25_scores[index],
            }
            debug_payload["bm25_candidates"].append(
                {
                    "document_title": metadata.get("document_title", ""),
                    "section_title": metadata.get("section_title", ""),
                    "chunk_index": metadata.get("chunk_index"),
                    "bm25_score": float(bm25_scores[index]),
                }
            )

        merged_items = list(merged_candidates.values())
        unique_texts = [item["document"] for item in merged_items]
        if not unique_texts:
            return [], debug_payload

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
                    metadata = merged_items[index]["metadata"]
                    debug_payload["reranked_results"].append(
                        {
                            "document_title": metadata.get("document_title", ""),
                            "section_title": metadata.get("section_title", ""),
                            "chunk_index": metadata.get("chunk_index"),
                            "rerank_score": item.relevance_score,
                        }
                    )
                    final_results.append(
                        {
                            "document": unique_texts[index],
                            "metadata": metadata,
                            "score": item.relevance_score,
                        }
                    )
                return final_results, debug_payload
        except Exception:
            pass

        fallback_results = []
        for item in merged_items[: self.final_top_k]:
            debug_payload["reranked_results"].append(
                {
                    "document_title": item["metadata"].get("document_title", ""),
                    "section_title": item["metadata"].get("section_title", ""),
                    "chunk_index": item["metadata"].get("chunk_index"),
                    "rerank_score": 0.0,
                }
            )
            fallback_results.append({"document": item["document"], "metadata": item["metadata"], "score": 0.0})
        return fallback_results, debug_payload

    def _build_candidate_key(self, document: str, metadata: dict) -> str:
        document_id = metadata.get("document_id")
        chunk_index = metadata.get("chunk_index")
        if document_id is not None and chunk_index is not None:
            return f"{document_id}::{chunk_index}"
        return document
