from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from parser.agent_doc_rag import (
    build_agent_doc_rag_index,
    query_agent_doc_rag_index,
)


class AgentDocRagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo_root = self.root / "repo"
        (self.repo_root / "doc" / "agent").mkdir(parents=True)
        (self.repo_root / "realization" / "docs" / "result").mkdir(parents=True)
        (self.repo_root / "realization" / "spec" / "schema").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_build_and_query_returns_artifact_paths_and_line_refs(self) -> None:
        source = self.repo_root / "doc" / "agent" / "plan.md"
        source.write_text(
            "\n".join(
                [
                    "# Runtime plan",
                    "local_model is deferred for this phase.",
                    "RAG output must cite artifact paths.",
                    "The helper is read-only and does not replace formal tests.",
                ]
            ),
            encoding="utf-8",
        )
        (self.repo_root / "realization" / "spec" / "schema" / "advisor_decision.schema.json").write_text(
            json.dumps({"title": "AdvisorDecision", "advisor_mode": ["heuristic"]}),
            encoding="utf-8",
        )

        index = build_agent_doc_rag_index(repo_root=self.repo_root, chunk_lines=3, overlap_lines=1)
        report = query_agent_doc_rag_index(index, query="local_model RAG artifact paths", top_k=2)

        self.assertEqual(index["summary"]["file_count"], 2)
        self.assertGreaterEqual(report["result_count"], 1)
        first = report["results"][0]
        self.assertEqual(first["artifact_path"], "doc/agent/plan.md")
        self.assertGreaterEqual(first["line_start"], 1)
        self.assertGreaterEqual(first["line_end"], first["line_start"])
        self.assertIn("artifact", first["snippet"])
        self.assertTrue(first["source_checksum"].startswith("sha256:"))

    def test_build_skips_large_and_unsupported_files(self) -> None:
        (self.repo_root / "doc" / "agent" / "small.md").write_text("RAG small doc\n", encoding="utf-8")
        (self.repo_root / "doc" / "agent" / "large.md").write_text("x" * 64, encoding="utf-8")
        (self.repo_root / "doc" / "agent" / "blob.bin").write_bytes(b"\x00\x01")

        index = build_agent_doc_rag_index(
            repo_root=self.repo_root,
            include_globs=["doc/agent/*"],
            max_file_bytes=16,
        )
        skipped = {row["artifact_path"]: row["reason"] for row in index["files_skipped"]}

        self.assertEqual(index["summary"]["file_count"], 1)
        self.assertEqual(skipped["doc/agent/large.md"], "file_too_large")
        self.assertEqual(skipped["doc/agent/blob.bin"], "unsupported_suffix")

    def test_query_without_terms_returns_empty_success_report(self) -> None:
        index = build_agent_doc_rag_index(repo_root=self.repo_root)
        report = query_agent_doc_rag_index(index, query="   ", top_k=5)

        self.assertEqual(report["result_count"], 0)
        self.assertEqual(report["results"], [])

    def test_cli_build_and_query_round_trip(self) -> None:
        (self.repo_root / "doc" / "agent" / "p9.md").write_text(
            "P9 RAG local index cites artifact path references.\n",
            encoding="utf-8",
        )
        realization_root = Path(__file__).resolve().parents[2]
        index_path = self.root / "index.json"
        query_path = self.root / "query.json"
        markdown_path = self.root / "query.md"

        subprocess.run(
            [
                sys.executable,
                "tool/build_agent_doc_rag_index.py",
                "--repo-root",
                str(self.repo_root),
                "--output",
                str(index_path),
                "--include",
                "doc/agent/**/*.md",
            ],
            cwd=realization_root,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "tool/query_agent_doc_rag.py",
                "--index",
                str(index_path),
                "--query",
                "P9 artifact references",
                "--output",
                str(query_path),
                "--markdown-output",
                str(markdown_path),
            ],
            cwd=realization_root,
            check=True,
        )

        query_payload = json.loads(query_path.read_text(encoding="utf-8"))
        self.assertEqual(query_payload["result_count"], 1)
        self.assertIn("doc/agent/p9.md", markdown_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
