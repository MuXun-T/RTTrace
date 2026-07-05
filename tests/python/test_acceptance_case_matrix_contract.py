from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_DOC = ROOT / "document" / "需求规格说明书_MVP_v1.3_回放对比复现对齐版.md"
MATRIX_PATH = ROOT / "docs" / "acceptance_case_matrix_20260329.json"

TC_LINE_PATTERN = re.compile(
    r"^\|\s*(TC-(?:COL|FMT|PRS|MET|VIZ|RPY|CMP|EXP|RPR)-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$"
)


def _requirements_tc_rows() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in REQUIREMENTS_DOC.read_text(encoding="utf-8").splitlines():
        matched = TC_LINE_PATTERN.match(line)
        if matched:
            rows.append((matched.group(1), matched.group(2).strip(), matched.group(3).strip()))
    return rows


def _load_matrix() -> dict[str, object]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _assert_symbol_exists(path: Path, symbol: str) -> None:
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        assert re.search(rf"^\s*def\s+{re.escape(symbol)}\s*\(", content, re.MULTILINE), (
            f"python test symbol not found: {symbol} in {path}"
        )
        return
    if path.suffix in {".cpp", ".cc", ".cxx", ".c"}:
        assert re.search(rf"\bbool\s+{re.escape(symbol)}\s*\(", content), (
            f"cpp test symbol not found: {symbol} in {path}"
        )
        return
    raise AssertionError(f"unsupported test file suffix for symbol check: {path}")


def test_acceptance_case_matrix_contract() -> None:
    requirement_rows = _requirements_tc_rows()
    assert len(requirement_rows) == 50, f"expected 50 TC rows in requirements doc, got {len(requirement_rows)}"

    requirement_ids = [row[0] for row in requirement_rows]
    requirement_id_set = set(requirement_ids)
    assert len(requirement_id_set) == 50, "requirements TC ids must be unique"

    matrix = _load_matrix()
    assert isinstance(matrix, dict), "matrix root must be object"

    tc_cases = matrix.get("tc_cases")
    assert isinstance(tc_cases, list), "tc_cases must be a list"
    assert len(tc_cases) == 50, f"expected 50 tc cases, got {len(tc_cases)}"

    matrix_ids: list[str] = []
    for index, case in enumerate(tc_cases):
        assert isinstance(case, dict), f"tc case at index {index} must be object"

        tc_id = case.get("tc_id")
        assert isinstance(tc_id, str) and tc_id.strip(), f"tc_id must be non-empty string at index {index}"
        matrix_ids.append(tc_id)

        title = case.get("title")
        acceptance_point = case.get("acceptance_point")
        assert isinstance(title, str) and title.strip(), f"title must be non-empty for {tc_id}"
        assert isinstance(acceptance_point, str) and acceptance_point.strip(), (
            f"acceptance_point must be non-empty for {tc_id}"
        )

        evidence = case.get("evidence")
        assert isinstance(evidence, list) and evidence, f"evidence must be non-empty list for {tc_id}"

        primary_count = 0
        for ev_index, item in enumerate(evidence):
            assert isinstance(item, dict), f"evidence item must be object for {tc_id}[{ev_index}]"
            role = item.get("role")
            ev_type = item.get("type")
            ref = item.get("ref")

            assert role in {"primary", "supporting"}, f"invalid role for {tc_id}[{ev_index}]"
            assert ev_type in {"test", "artifact", "manual"}, f"invalid type for {tc_id}[{ev_index}]"
            assert isinstance(ref, str) and ref.strip(), f"ref must be non-empty for {tc_id}[{ev_index}]"

            if role == "primary":
                primary_count += 1

            if ev_type == "test":
                assert "::" in ref, f"test ref must include '::' for {tc_id}[{ev_index}]"
                path_str, symbol = ref.rsplit("::", 1)
                test_path = ROOT / path_str
                assert test_path.exists(), f"test file missing for {tc_id}[{ev_index}]: {test_path}"
                _assert_symbol_exists(test_path, symbol)
            elif ev_type == "artifact":
                artifact_path = ROOT / ref
                assert artifact_path.exists(), f"artifact missing for {tc_id}[{ev_index}]: {artifact_path}"
                if role == "primary":
                    assert not re.match(r"^docs/acceptance_baseline_stage", ref), (
                        f"stage acceptance artifact cannot be primary for {tc_id}[{ev_index}]"
                    )

        assert primary_count >= 1, f"each tc must have at least one primary evidence: {tc_id}"

    matrix_id_set = set(matrix_ids)
    assert len(matrix_id_set) == 50, "matrix TC ids must be unique"
    assert matrix_id_set == requirement_id_set, "matrix TC id set must match requirements TC id set exactly"

