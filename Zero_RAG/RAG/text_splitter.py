from . import config


class SemanticTextSplitter:
    def __init__(self, chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap, separators=config.separators):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # 默认按段落、换行、句号等符号逐级拆分
        self.separators = separators or ["\n\n", "\n", " ", "", ",", ".", "!", "?", "，", "。", "！", "？"]

    def split_text(self, text: str) -> list[str]:
        return self._split_text_with_separators(text, self.separators)

    def _split_text_with_separators(self, text: str, separators: list[str]) -> list[str]:
        # 寻找当前层级可用的分隔符
        separator = ""
        for sep in separators:
            if sep in text:
                separator = sep
                break

        # 如果没有找到分隔符，或者当前文本已经小于 chunk_size，则不继续拆分
        if not separator or len(text) <= self.chunk_size:
            return [text]

        # 按照找到的最高优先级分隔符切分文本
        splits = text.split(separator)

        # 过滤掉空字符串，并保留分隔符（除了换行符通常不作为句意本身）
        good_splits = []
        for s in splits:
            if s.strip():
                # 可以选择将分隔符拼回去，保持标点完整
                good_splits.append(s + (separator if separator not in ["\n\n", "\n", " "] else ""))

        return self._merge_splits(good_splits, separator, separators)

    def _merge_splits(self, splits: list[str], separator: str, separators: list[str]) -> list[str]:
        chunks = []
        current_chunk = []
        current_length = 0

        for split in splits:
            # 如果单个拆分本身就超过了块大小，需要按下一级分隔符继续递归拆分
            if len(split) > self.chunk_size:
                if current_chunk:
                    chunks.append("".join(current_chunk).strip())
                    current_chunk = []
                    current_length = 0

                next_separators = separators[separators.index(separator) + 1:] if separator in separators else []
                if next_separators:
                    sub_chunks = self._split_text_with_separators(split, next_separators)
                    chunks.extend(sub_chunks)
                else:
                    # 如果没有更多级别的分隔符，只能按字符强制截断
                    yield_chunks = [split[i:i + self.chunk_size] for i in range(0, len(split), self.chunk_size)]
                    chunks.extend(yield_chunks)
                continue

            # 如果加入当前块会超出限制，则生成一个新的块
            if current_length + len(split) > self.chunk_size and current_chunk:
                chunks.append("".join(current_chunk).strip())

                # 处理 overlap
                overlap_length = 0
                overlap_chunk = []
                for item in reversed(current_chunk):
                    if overlap_length + len(item) <= self.chunk_overlap:
                        overlap_chunk.insert(0, item)
                        overlap_length += len(item)
                    else:
                        break

                current_chunk = overlap_chunk
                current_length = overlap_length

            current_chunk.append(split)
            current_length += len(split)

        if current_chunk:
            chunks.append("".join(current_chunk).strip())

        return chunks
