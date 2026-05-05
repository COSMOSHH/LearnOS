import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = ROOT_DIR / "datasets" / "scifact" / "scifact"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "benchmarks" / "scifact"


def _read_jsonl(path: Path) -> dict[str, dict]:
    rows = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            rows[str(item["_id"])] = item
    return rows


def _read_qrels(path: Path) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            query_id = str(row.get("query-id", "")).strip()
            corpus_id = str(row.get("corpus-id", "")).strip()
            if not query_id or not corpus_id:
                continue
            qrels[query_id][corpus_id] = int(float(row.get("score") or 1))
    return dict(qrels)


def _safe_title(item: dict, doc_id: str) -> str:
    title = (item.get("title") or "").strip()
    return title or f"SciFact document {doc_id}"


def build_eval_cases(dataset_dir: Path, split: str = "test", max_cases: int | None = None) -> list[dict]:
    queries = _read_jsonl(dataset_dir / "queries.jsonl")
    corpus = _read_jsonl(dataset_dir / "corpus.jsonl")
    qrels = _read_qrels(dataset_dir / "qrels" / f"{split}.tsv")

    cases = []
    for query_id in sorted(qrels.keys(), key=lambda value: int(value) if value.isdigit() else value):
        query = queries.get(query_id)
        if not query:
            continue
        relevant_scores = qrels[query_id]
        relevant_doc_ids = list(relevant_scores.keys())
        relevant_titles = [_safe_title(corpus[doc_id], doc_id) for doc_id in relevant_doc_ids if doc_id in corpus]

        cases.append(
            {
                "query": query.get("text", "").strip(),
                "relevant_doc_ids": relevant_doc_ids,
                "relevant_scores": relevant_scores,
                "relevant_sources": [f"scifact:{doc_id}" for doc_id in relevant_doc_ids],
                "relevant_titles": relevant_titles,
                "relevant_keywords": relevant_titles[:3],
                "topic_keywords": ["scifact", "science", "biomedical"],
                "case_type": "beir_scifact",
                "metadata": {
                    "dataset": "scifact",
                    "split": split,
                    "query_id": query_id,
                },
            }
        )
        if max_cases and len(cases) >= max_cases:
            break
    return cases


def build_corpus_markdown(dataset_dir: Path, max_documents: int | None = None) -> str:
    corpus = _read_jsonl(dataset_dir / "corpus.jsonl")
    lines = [
        "# SciFact Fixed RAG Benchmark Corpus",
        "",
        "This file is generated from the BEIR SciFact corpus for LearnOS fixed RAG benchmark runs.",
        "Each document heading includes the SciFact corpus id so qrels can be matched against retrieved heading paths.",
        "",
    ]

    for index, doc_id in enumerate(sorted(corpus.keys(), key=lambda value: int(value) if value.isdigit() else value), start=1):
        if max_documents and index > max_documents:
            break
        item = corpus[doc_id]
        title = _safe_title(item, doc_id)
        text = (item.get("text") or "").strip()
        lines.extend(
            [
                f"## scifact:{doc_id} | {title}",
                "",
                text,
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def prepare_scifact_benchmark(
    dataset_dir: Path = DEFAULT_INPUT_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    split: str = "test",
    max_cases: int | None = None,
    max_documents: int | None = None,
) -> dict:
    dataset_dir = dataset_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = build_eval_cases(dataset_dir, split=split, max_cases=max_cases)
    corpus_markdown = build_corpus_markdown(dataset_dir, max_documents=max_documents)

    cases_path = output_dir / f"scifact_{split}_eval_cases.json"
    corpus_path = output_dir / "scifact_corpus.md"
    manifest_path = output_dir / "manifest.json"

    cases_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    corpus_path.write_text(corpus_markdown, encoding="utf-8")
    manifest = {
        "dataset": "scifact",
        "split": split,
        "case_count": len(cases),
        "corpus_path": str(corpus_path),
        "cases_path": str(cases_path),
        "source_dataset_dir": str(dataset_dir),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Prepare BEIR SciFact files for LearnOS fixed RAG benchmark runs.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--split", default="test", choices=["test", "train"])
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--max-documents", type=int, default=None)
    args = parser.parse_args()

    manifest = prepare_scifact_benchmark(
        dataset_dir=args.dataset_dir,
        output_dir=args.output_dir,
        split=args.split,
        max_cases=args.max_cases,
        max_documents=args.max_documents,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
