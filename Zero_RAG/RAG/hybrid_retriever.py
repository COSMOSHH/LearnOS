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

    def retrieve_with_debug(
        self,
        query: str,
        queries: list[str] | None = None,
        vector_top_k: int | None = None,
        bm25_top_k: int | None = None,
        final_top_k: int | None = None,
        parent_window: int | None = None,
        parent_max_chars: int | None = None,
        use_bm25: bool = True,
        use_rerank: bool = True,
        use_parent: bool = True,
    ) -> tuple[List[Dict], Dict]:
        query_candidates = queries or [query]
        vector_top_k = max(1, int(vector_top_k or self.vector_top_k))
        bm25_top_k = max(1, int(bm25_top_k or self.bm25_top_k))
        final_top_k = max(1, int(final_top_k or self.final_top_k))
        parent_window = max(0, int(parent_window if parent_window is not None else 1))
        parent_max_chars = max(200, int(parent_max_chars or 900))
        debug_payload = {
            "query": query,
            "query_strategy": "multi_query" if len(query_candidates) > 1 else "single_query",
            "expanded_queries": query_candidates,
            "vector_top_k": vector_top_k,
            "bm25_top_k": bm25_top_k,
            "final_top_k": final_top_k,
            "vector_candidates": [],
            "bm25_candidates": [],
            "reranked_results": [],
            "parent_enriched": 0,
            "parent_strategy": {
                "window": parent_window,
                "max_chars": parent_max_chars,
            },
            "parent_debug": [],
            "retrieval_config": {
                "use_bm25": use_bm25,
                "use_rerank": use_rerank,
                "use_parent": use_parent,
            },
        }

        merged_candidates = {}
        for expanded_query in query_candidates:
            vector_results = self.vector_store.similarity_search(
                expanded_query,
                top_k=vector_top_k,
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

            if use_bm25:
                tokenized_query = list(jieba.cut(expanded_query))
                bm25_scores = self.bm25.get_scores(tokenized_query) if self.doc_chunks else []
                bm25_top_n_idx = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[:bm25_top_k]

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
        unique_texts = [
            self._expand_to_parent_document(
                item["document"],
                item["metadata"],
                debug_payload,
                parent_window=parent_window,
                parent_max_chars=parent_max_chars,
            ) if use_parent else item["document"]
            for item in merged_items
        ]
        if not unique_texts:
            return [], debug_payload

        if use_rerank:
            try:
                response = dashscope.TextReRank.call(
                    model=config.rerank_model,
                    query=query,
                    documents=unique_texts,
                    top_n=final_top_k,
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

        ranked_indices = sorted(
            range(len(merged_items)),
            key=lambda index: self._fallback_rank_key(merged_items[index]),
            reverse=True,
        )
        fallback_results = []
        for index in ranked_indices[:final_top_k]:
            item = merged_items[index]
            fallback_score = self._fallback_score(item)
            debug_payload["reranked_results"].append(
                {
                    "document_title": item["metadata"].get("document_title", ""),
                    "section_title": item["metadata"].get("section_title", ""),
                    "chunk_index": item["metadata"].get("chunk_index"),
                    "rerank_score": fallback_score,
                    "rank_source": "rerank_disabled" if not use_rerank else "rerank_fallback",
                }
            )
            fallback_results.append({"document": unique_texts[index], "metadata": item["metadata"], "score": fallback_score})
        return fallback_results, debug_payload

    def _fallback_score(self, item: dict) -> float:
        vector_distance = item.get("vector_distance")
        bm25_score = float(item.get("bm25_score") or 0)
        vector_score = 0.0
        if vector_distance is not None:
            vector_score = max(0.0, 1.0 - float(vector_distance))
        return vector_score + min(1.0, bm25_score / 100.0)

    def _fallback_rank_key(self, item: dict) -> tuple:
        vector_distance = item.get("vector_distance")
        has_vector = vector_distance is not None
        vector_score = 0.0 if vector_distance is None else -float(vector_distance)
        bm25_score = float(item.get("bm25_score") or 0)
        return (1 if has_vector else 0, vector_score, bm25_score)

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
            sections.setdefault(section_key, []).append(
                {
                    "text": chunk["chunk_text"],
                    "chunk_index": self._safe_int(metadata.get("chunk_index")),
                    "metadata": metadata,
                }
            )
        for section_chunks in sections.values():
            section_chunks.sort(key=lambda item: item["chunk_index"])
        return sections

    def _build_section_key(self, metadata: dict) -> str:
        document_id = metadata.get("document_id")
        heading_path = metadata.get("heading_path") or metadata.get("section_title")
        if not document_id or not heading_path:
            return ""
        return f"{document_id}::{heading_path}"

    def _expand_to_parent_document(
        self,
        document: str,
        metadata: dict,
        debug_payload: dict,
        parent_window: int = 1,
        parent_max_chars: int = 900,
    ) -> str:
        section_key = self._build_section_key(metadata)
        parent_chunks = self.parent_sections.get(section_key, [])
        if len(parent_chunks) <= 1:
            return document

        hit_chunk_index = self._safe_int(metadata.get("chunk_index"))
        hit_position = 0
        for index, item in enumerate(parent_chunks):
            if item["chunk_index"] == hit_chunk_index:
                hit_position = index
                break

        window_start = max(0, hit_position - parent_window)
        window_end = min(len(parent_chunks), hit_position + parent_window + 1)
        selected_chunks = parent_chunks[window_start:window_end]
        selected_texts = [item["text"] for item in selected_chunks if item.get("text")]
        joined = "\n\n".join(selected_texts) or document

        truncated = False
        if len(joined) > parent_max_chars:
            truncated = True
            hit_text = document[: min(len(document), parent_max_chars)]
            neighbor_budget = max(0, parent_max_chars - len(hit_text) - 2)
            neighbor_text = "\n\n".join(text for text in selected_texts if text != document)
            joined = f"{hit_text}\n\n{neighbor_text[:neighbor_budget]}".strip()[:parent_max_chars]

        debug_payload["parent_enriched"] += 1
        debug_payload["parent_debug"].append(
            {
                "document_id": metadata.get("document_id", ""),
                "heading_path": metadata.get("heading_path") or metadata.get("section_title", ""),
                "hit_chunk_index": hit_chunk_index,
                "selected_count": len(selected_texts),
                "window_start": window_start,
                "window_end": max(window_start, window_end - 1),
                "parent_chars": len(joined),
                "truncated": truncated,
            }
        )
        return joined

    def _safe_int(self, value, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default
