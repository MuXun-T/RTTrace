from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


RAG_INDEX_VERSION = "agent-doc-rag-index-v0"
RAG_QUERY_VERSION = "agent-doc-rag-query-v0"
DEFAULT_INCLUDE_GLOBS = (
    "doc/agent/**/*.md",
    "realization/docs/*.md",
    "realization/docs/*.json",
    "realization/docs/result/**/*.md",
    "realization/docs/result/**/*.json",
    "realization/spec/schema/*.json",
)
DEFAULT_EXCLUDE_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "evidence_proof_archive_20260414",
    "evidence_proof_archive_20260506",
    "package_linux_formal",
    "package_windows_formal",
}
TEXT_SUFFIXES = {".json", ".md", ".txt"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _is_excluded(path: Path, root: Path, exclude_parts: Iterable[str]) -> bool:
    relative_parts = set(path.resolve().relative_to(root.resolve()).parts)
    return bool(relative_parts.intersection(set(exclude_parts)))


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens: list[str] = []
    tokens.extend(re.findall(r"[a-z0-9_][a-z0-9_.:-]{1,}", lowered))
    for cjk_run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        tokens.extend(cjk_run)
        tokens.extend(cjk_run[index : index + 2] for index in range(max(0, len(cjk_run) - 1)))
    return tokens


def _chunk_lines(lines: list[str], *, chunk_lines: int, overlap_lines: int) -> Iterable[tuple[int, int, str]]:
    step = max(1, chunk_lines - overlap_lines)
    for start in range(0, len(lines), step):
        end = min(len(lines), start + chunk_lines)
        text = "\n".join(lines[start:end]).strip()
        if text:
            yield start + 1, end, text
        if end >= len(lines):
            break


def build_agent_doc_rag_index(
    *,
    repo_root: str | Path,
    include_globs: Iterable[str] | None = None,
    exclude_parts: Iterable[str] | None = None,
    max_file_bytes: int = 1024 * 1024,
    chunk_lines: int = 32,
    overlap_lines: int = 4,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    includes = tuple(include_globs or DEFAULT_INCLUDE_GLOBS)
    excludes = tuple(exclude_parts or DEFAULT_EXCLUDE_PARTS)
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in includes:
        for path in sorted(root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            files.append(path)

    chunks: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    indexed_files: list[dict[str, Any]] = []
    for path in files:
        artifact_path = _safe_relative(path, root)
        try:
            if _is_excluded(path, root, excludes):
                skipped.append({"artifact_path": artifact_path, "reason": "excluded_path"})
                continue
        except ValueError:
            skipped.append({"artifact_path": artifact_path, "reason": "outside_repo_root"})
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            skipped.append({"artifact_path": artifact_path, "reason": "unsupported_suffix"})
            continue
        size_bytes = path.stat().st_size
        if size_bytes > max_file_bytes:
            skipped.append({"artifact_path": artifact_path, "reason": "file_too_large", "bytes": size_bytes})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append({"artifact_path": artifact_path, "reason": "utf8_decode_failed", "bytes": size_bytes})
            continue
        source_checksum = _sha256_text(text)
        lines = text.splitlines()
        file_chunk_count = 0
        for line_start, line_end, chunk_text in _chunk_lines(
            lines,
            chunk_lines=chunk_lines,
            overlap_lines=overlap_lines,
        ):
            terms = Counter(_tokenize(chunk_text))
            if not terms:
                continue
            chunks.append(
                {
                    "chunk_id": f"chunk:{len(chunks) + 1}",
                    "artifact_path": artifact_path,
                    "line_start": line_start,
                    "line_end": line_end,
                    "source_checksum": source_checksum,
                    "text": chunk_text,
                    "term_counts": dict(sorted(terms.items())),
                }
            )
            file_chunk_count += 1
        indexed_files.append(
            {
                "artifact_path": artifact_path,
                "bytes": size_bytes,
                "line_count": len(lines),
                "chunk_count": file_chunk_count,
                "source_checksum": source_checksum,
            }
        )

    return {
        "index_version": RAG_INDEX_VERSION,
        "generated_at": _iso_now(),
        "repo_root": str(root),
        "include_globs": list(includes),
        "exclude_parts": list(excludes),
        "max_file_bytes": int(max_file_bytes),
        "chunk_lines": int(chunk_lines),
        "overlap_lines": int(overlap_lines),
        "files_indexed": indexed_files,
        "files_skipped": skipped,
        "chunks": chunks,
        "summary": {
            "file_count": len(indexed_files),
            "skipped_file_count": len(skipped),
            "chunk_count": len(chunks),
        },
    }


def write_agent_doc_rag_index(path: str | Path, index: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_agent_doc_rag_index(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("RAG index must be a JSON object")
    if payload.get("index_version") != RAG_INDEX_VERSION:
        raise ValueError(f"unsupported RAG index version: {payload.get('index_version')}")
    return payload


def _document_frequency(chunks: list[dict[str, Any]]) -> Counter[str]:
    df: Counter[str] = Counter()
    for chunk in chunks:
        df.update(set(dict(chunk.get("term_counts") or {}).keys()))
    return df


def _snippet(text: str, query_terms: list[str], max_chars: int) -> str:
    lowered = text.lower()
    offsets = [lowered.find(term.lower()) for term in query_terms if len(term) >= 2 and lowered.find(term.lower()) >= 0]
    start = max(0, min(offsets) - 80) if offsets else 0
    snippet = text[start : start + max_chars].strip()
    if start > 0:
        snippet = "..." + snippet
    if start + max_chars < len(text):
        snippet = snippet + "..."
    return snippet.replace("\n", " ")


def query_agent_doc_rag_index(
    index: dict[str, Any],
    *,
    query: str,
    top_k: int = 5,
    max_snippet_chars: int = 360,
) -> dict[str, Any]:
    query_terms = _tokenize(query)
    chunks = [dict(chunk) for chunk in list(index.get("chunks") or []) if isinstance(chunk, dict)]
    if not query_terms:
        results: list[dict[str, Any]] = []
    else:
        query_counts = Counter(query_terms)
        df = _document_frequency(chunks)
        total = max(1, len(chunks))
        scored: list[tuple[float, dict[str, Any]]] = []
        lowered_query = query.lower().strip()
        for chunk in chunks:
            terms = dict(chunk.get("term_counts") or {})
            score = 0.0
            for term, query_count in query_counts.items():
                term_count = int(terms.get(term) or 0)
                if term_count <= 0:
                    continue
                idf = 1.0 + math.log(total / max(1, int(df.get(term) or 1)))
                score += float(query_count * term_count) * idf
            text = str(chunk.get("text") or "")
            if lowered_query and lowered_query in text.lower():
                score += 5.0
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("artifact_path")), int(item[1].get("line_start") or 0)))
        results = [
            {
                "artifact_path": str(chunk.get("artifact_path")),
                "line_start": int(chunk.get("line_start") or 0),
                "line_end": int(chunk.get("line_end") or 0),
                "score": round(score, 6),
                "snippet": _snippet(str(chunk.get("text") or ""), query_terms, max_snippet_chars),
                "source_checksum": str(chunk.get("source_checksum")),
                "chunk_id": str(chunk.get("chunk_id")),
            }
            for score, chunk in scored[: max(0, int(top_k))]
        ]
    return {
        "query_version": RAG_QUERY_VERSION,
        "generated_at": _iso_now(),
        "index_version": index.get("index_version"),
        "query": query,
        "top_k": int(top_k),
        "result_count": len(results),
        "results": results,
    }


def format_rag_query_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agent Doc RAG Query",
        "",
        f"- Query: `{report.get('query')}`",
        f"- Results: {report.get('result_count')}",
        "",
    ]
    for index, result in enumerate(list(report.get("results") or []), start=1):
        lines.extend(
            [
                f"## {index}. {result.get('artifact_path')}:{result.get('line_start')}",
                "",
                f"- Score: {result.get('score')}",
                f"- Lines: {result.get('line_start')}-{result.get('line_end')}",
                f"- Source checksum: `{result.get('source_checksum')}`",
                "",
                str(result.get("snippet") or ""),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
