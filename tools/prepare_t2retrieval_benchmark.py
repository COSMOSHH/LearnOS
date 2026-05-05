import argparse
import json
from collections import defaultdict
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = ROOT_DIR / "datasets" / "t2retrieval"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "benchmarks" / "t2retrieval"


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("This tool requires pandas and pyarrow: pip install pandas pyarrow") from exc
    return pd


def _require_datasets():
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("This mode requires datasets: pip install datasets") from exc
    return load_dataset


def _read_table(path: Path):
    pd = _require_pandas()
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() in {".jsonl", ".json"}:
        return pd.read_json(path, lines=path.suffix.lower() == ".jsonl")
    if path.suffix.lower() in {".tsv", ".csv"}:
        return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",")
    raise ValueError(f"Unsupported file format: {path}")


def _dataset_to_dataframe(dataset):
    pd = _require_pandas()
    return pd.DataFrame(dataset)


def _find_first_file(folder: Path, preferred_names: list[str]) -> Path:
    for name in preferred_names:
        candidate = folder / name
        if candidate.exists():
            return candidate
    for pattern in ["*.parquet", "*.jsonl", "*.tsv", "*.csv", "*.json"]:
        matches = sorted(folder.glob(pattern))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"No supported dataset file found in {folder}")


def _pick_column(columns, candidates: list[str]) -> str:
    normalized = {column.lower().replace("_", "-"): column for column in columns}
    for candidate in candidates:
        key = candidate.lower().replace("_", "-")
        if key in normalized:
            return normalized[key]
    raise KeyError(f"None of the columns {candidates} exist in {list(columns)}")


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def prepare_t2retrieval_benchmark(
    dataset_dir: Path = DEFAULT_INPUT_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    max_cases: int = 200,
    max_documents: int = 5000,
) -> dict:
    dataset_dir = dataset_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus_path = _find_first_file(dataset_dir / "corpus", ["dev-00000-of-00001.parquet"])
    queries_path = _find_first_file(dataset_dir / "queries", ["dev-00000-of-00001.parquet"])
    qrels_path = _find_first_file(dataset_dir / "data", ["dev-00000-of-00001.parquet"])

    corpus_df = _read_table(corpus_path)
    queries_df = _read_table(queries_path)
    qrels_df = _read_table(qrels_path)

    corpus_id_col = _pick_column(corpus_df.columns, ["_id", "id", "pid", "corpus-id", "corpus_id"])
    corpus_text_col = _pick_column(corpus_df.columns, ["text", "contents", "content", "document"])
    corpus_title_col = None
    try:
        corpus_title_col = _pick_column(corpus_df.columns, ["title"])
    except KeyError:
        pass

    query_id_col = _pick_column(queries_df.columns, ["_id", "id", "qid", "query-id", "query_id"])
    query_text_col = _pick_column(queries_df.columns, ["text", "query"])

    qrel_query_col = _pick_column(qrels_df.columns, ["query-id", "query_id", "qid"])
    qrel_corpus_col = _pick_column(qrels_df.columns, ["corpus-id", "corpus_id", "pid"])
    qrel_score_col = _pick_column(qrels_df.columns, ["score"])

    query_text_by_id = {
        str(row[query_id_col]): _safe_text(row[query_text_col])
        for _, row in queries_df.iterrows()
    }
    corpus_by_id = {
        str(row[corpus_id_col]): {
            "title": _safe_text(row[corpus_title_col]) if corpus_title_col else f"T2Retrieval document {row[corpus_id_col]}",
            "text": _safe_text(row[corpus_text_col]),
        }
        for _, row in corpus_df.iterrows()
    }

    qrels_by_query: dict[str, dict[str, int]] = defaultdict(dict)
    for _, row in qrels_df.iterrows():
        query_id = str(row[qrel_query_col])
        corpus_id = str(row[qrel_corpus_col])
        if query_id in query_text_by_id and corpus_id in corpus_by_id:
            qrels_by_query[query_id][corpus_id] = int(row[qrel_score_col])

    cases = []
    required_doc_ids = set()
    for query_id in sorted(qrels_by_query.keys(), key=lambda value: int(value) if value.isdigit() else value):
        relevant_scores = qrels_by_query[query_id]
        relevant_doc_ids = list(relevant_scores.keys())
        required_doc_ids.update(relevant_doc_ids)
        cases.append(
            {
                "query": query_text_by_id[query_id],
                "relevant_doc_ids": relevant_doc_ids,
                "relevant_scores": relevant_scores,
                "relevant_sources": [f"t2retrieval:{doc_id}" for doc_id in relevant_doc_ids],
                "relevant_titles": [corpus_by_id[doc_id]["title"] for doc_id in relevant_doc_ids[:5]],
                "relevant_keywords": [query_text_by_id[query_id]],
                "topic_keywords": ["t2retrieval", "chinese", "中文检索"],
                "case_type": "mteb_t2retrieval",
                "metadata": {"dataset": "t2retrieval", "split": "dev", "query_id": query_id},
            }
        )
        if len(cases) >= max_cases:
            break

    selected_doc_ids = []
    seen_doc_ids = set()
    for doc_id in sorted(required_doc_ids, key=lambda value: int(value) if value.isdigit() else value):
        selected_doc_ids.append(doc_id)
        seen_doc_ids.add(doc_id)
    for doc_id in sorted(corpus_by_id.keys(), key=lambda value: int(value) if value.isdigit() else value):
        if len(selected_doc_ids) >= max_documents:
            break
        if doc_id not in seen_doc_ids:
            selected_doc_ids.append(doc_id)
            seen_doc_ids.add(doc_id)

    lines = [
        "# T2Retrieval Chinese Fixed RAG Benchmark Corpus",
        "",
        "This file is generated from mteb/T2Retrieval for LearnOS Chinese RAG benchmark runs.",
        "Each heading includes the corpus id so qrels can be matched against retrieved heading paths.",
        "",
    ]
    for doc_id in selected_doc_ids:
        item = corpus_by_id[doc_id]
        lines.extend([f"## t2retrieval:{doc_id} | {item['title']}", "", item["text"], ""])

    corpus_markdown = "\n".join(lines).strip() + "\n"
    corpus_output_path = output_dir / "t2retrieval_corpus.md"
    cases_output_path = output_dir / "t2retrieval_dev_eval_cases.json"
    manifest_path = output_dir / "manifest.json"
    corpus_output_path.write_text(corpus_markdown, encoding="utf-8")
    cases_output_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "dataset": "t2retrieval",
        "split": "dev",
        "case_count": len(cases),
        "document_count": len(selected_doc_ids),
        "corpus_path": str(corpus_output_path),
        "cases_path": str(cases_output_path),
        "source_dataset_dir": str(dataset_dir),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def prepare_t2retrieval_benchmark_from_hf(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    max_cases: int = 200,
    max_documents: int = 5000,
    dataset_name: str = "C-MTEB/T2Retrieval",
    qrels_name: str = "C-MTEB/T2Retrieval-qrels",
) -> dict:
    load_dataset = _require_datasets()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus_df = _dataset_to_dataframe(load_dataset(dataset_name, split="corpus"))
    queries_df = _dataset_to_dataframe(load_dataset(dataset_name, split="queries"))
    qrels_df = _dataset_to_dataframe(load_dataset(qrels_name, split="dev"))

    temp_dir = output_dir / "_hf_cache_tables"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / "corpus").mkdir(parents=True, exist_ok=True)
    (temp_dir / "queries").mkdir(parents=True, exist_ok=True)
    (temp_dir / "data").mkdir(parents=True, exist_ok=True)
    corpus_df.to_parquet(temp_dir / "corpus" / "dev-00000-of-00001.parquet", index=False)
    queries_df.to_parquet(temp_dir / "queries" / "dev-00000-of-00001.parquet", index=False)
    qrels_df.to_parquet(temp_dir / "data" / "dev-00000-of-00001.parquet", index=False)
    return prepare_t2retrieval_benchmark(
        dataset_dir=temp_dir,
        output_dir=output_dir,
        max_cases=max_cases,
        max_documents=max_documents,
    )


def main():
    parser = argparse.ArgumentParser(description="Prepare mteb/T2Retrieval files for LearnOS Chinese RAG benchmark runs.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-cases", type=int, default=200)
    parser.add_argument("--max-documents", type=int, default=5000)
    parser.add_argument("--from-hf", action="store_true", help="Load C-MTEB/T2Retrieval directly through datasets.load_dataset.")
    parser.add_argument("--dataset-name", default="C-MTEB/T2Retrieval")
    parser.add_argument("--qrels-name", default="C-MTEB/T2Retrieval-qrels")
    args = parser.parse_args()
    if args.from_hf:
        manifest = prepare_t2retrieval_benchmark_from_hf(
            output_dir=args.output_dir,
            max_cases=args.max_cases,
            max_documents=args.max_documents,
            dataset_name=args.dataset_name,
            qrels_name=args.qrels_name,
        )
    else:
        manifest = prepare_t2retrieval_benchmark(
            dataset_dir=args.dataset_dir,
            output_dir=args.output_dir,
            max_cases=args.max_cases,
            max_documents=args.max_documents,
        )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
