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
        self.parent_sections = self._build_parent_sections()

    def retrieve(self, query: str) -> List[Dict]:
        results, _ = self.retrieve_with_debug(query)
        return results

    def retrieve_with_debug(self, query: str, queries: list[str] | None = None) -> tuple[List[Dict], Dict]:
        query_candidates = queries or [query]
        debug_payload = {
            "query": query,
            "query_strategy": "multi_query" if len(query_candidates) > 1 else "single_query",
            "expanded_queries": query_candidates,
            "vector_top_k": self.vector_top_k,
            "bm25_top_k": self.bm25_top_k,
            "final_top_k": self.final_top_k,
            "vector_candidates": [],
            "bm25_candidates": [],
            "reranked_results": [],
            "parent_enriched": 0,
        }

        merged_candidates = {}
        for expanded_query in query_candidates:
            vector_results = self.vector_store.similarity_search(
                expanded_query,
                top_k=self.vector_top_k,
                where=self.vector_where,
            )
            for result in vector_results:
                metadata = result.get("metadata") or {}
                candidate_key = self._build_candidate_key(result["document"], metadata)
                merged_candidates[candidate_key] = {
                    "document": result["document"],
                    "metadata": metadata,
                    "vector_distance": result.get("distance"),
                    "bm25_score": merged_candidates.get(candidate_key, {}).get("bm25_score"),
                }
                debug_payload["vector_candidates"].append(
                    {
                        "query": expanded_query,
                        "document_title": metadata.get("document_title", ""),
                        "section_title": metadata.get("section_title", ""),
                        "chunk_index": metadata.get("chunk_index"),
                        "distance": result.get("distance"),
                    }
                )

            tokenized_query = list(jieba.cut(expanded_query))
            bm25_scores = self.bm25.get_scores(tokenized_query) if self.doc_chunks else []
            bm25_top_n_idx = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[: self.bm25_top_k]

            for index in bm25_top_n_idx:
                chunk = self.doc_chunks[index]
                metadata = chunk.get("metadata") or {}
                candidate_key = self._build_candidate_key(chunk["chunk_text"], metadata)
                previous_score = merged_candidates.get(candidate_key, {}).get("bm25_score")
                merged_candidates[candidate_key] = {
                    "document": chunk["chunk_text"],
                    "metadata": metadata,
                    "vector_distance": merged_candidates.get(candidate_key, {}).get("vector_distance"),
                    "bm25_score": max(float(bm25_scores[index]), float(previous_score or 0)),
                }
                debug_payload["bm25_candidates"].append(
                    {
                        "query": expanded_query,
                        "document_title": metadata.get("document_title", ""),
                        "section_title": metadata.get("section_title", ""),
                        "chunk_index": metadata.get("chunk_index"),
                        "bm25_score": float(bm25_scores[index]),
                    }
                )

        merged_items = list(merged_candidates.values())
        unique_texts = [self._expand_to_parent_document(item["document"], item["metadata"], debug_payload) for item in merged_items]
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

    def _build_parent_sections(self) -> dict:
        sections = {}
        for chunk in self.doc_chunks:
            metadata = chunk.get("metadata") or {}
            section_key = self._build_section_key(metadata)
            if not section_key:
                continue
            sections.setdefault(section_key, []).append(chunk["chunk_text"])
        return sections

    def _build_section_key(self, metadata: dict) -> str:
        document_id = metadata.get("document_id")
        heading_path = metadata.get("heading_path") or metadata.get("section_title")
        if not document_id or not heading_path:
            return ""
        return f"{document_id}::{heading_path}"

    def _expand_to_parent_document(self, document: str, metadata: dict, debug_payload: dict) -> str:
        section_key = self._build_section_key(metadata)
        parent_chunks = self.parent_sections.get(section_key, [])
        if len(parent_chunks) <= 1:
            return document
        debug_payload["parent_enriched"] += 1
        joined = "\n\n".join(parent_chunks)
        if len(joined) > 900:
            return joined[:900]
        return joined
