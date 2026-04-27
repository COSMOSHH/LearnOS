import re


SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？.!?])")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _token_set(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text or "")}


def _token_overlap_ratio(left: str, right: str) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    return intersection / max(1, min(len(left_tokens), len(right_tokens)))


def _compress_text(text: str, max_chars: int = 420) -> str:
    normalized = _normalize(text)
    if len(normalized) <= max_chars:
        return normalized

    sentences = [part.strip() for part in SENTENCE_SPLIT_PATTERN.split(normalized) if part.strip()]
    if not sentences:
        return normalized[:max_chars]

    compressed = []
    current_length = 0
    for sentence in sentences:
        if current_length + len(sentence) > max_chars and compressed:
            break
        compressed.append(sentence)
        current_length += len(sentence)
    return "".join(compressed)[:max_chars]


def build_generation_context(
    retrieved_results: list[dict],
    max_context_chars: int = 1800,
    per_chunk_max_chars: int = 420,
) -> tuple[list[dict], dict]:
    deduped_results = []
    used_texts = []
    total_chars = 0
    debug_payload = {"before_count": len(retrieved_results), "after_count": 0, "deduped": 0, "truncated": 0}

    for item in retrieved_results:
        candidate_text = _normalize(item.get("document", ""))
        if not candidate_text:
            continue

        is_duplicate = False
        for existing in used_texts:
            if _token_overlap_ratio(candidate_text, existing) >= 0.85:
                is_duplicate = True
                break
        if is_duplicate:
            debug_payload["deduped"] += 1
            continue

        compressed_text = _compress_text(candidate_text, max_chars=per_chunk_max_chars)
        if compressed_text != candidate_text:
            debug_payload["truncated"] += 1

        if total_chars + len(compressed_text) > max_context_chars and deduped_results:
            break

        deduped_results.append(
            {
                **item,
                "document": compressed_text,
                "metadata": {**(item.get("metadata") or {}), "compression_applied": compressed_text != candidate_text},
            }
        )
        used_texts.append(compressed_text)
        total_chars += len(compressed_text)

    debug_payload["after_count"] = len(deduped_results)
    debug_payload["final_context_chars"] = total_chars
    return deduped_results, debug_payload
