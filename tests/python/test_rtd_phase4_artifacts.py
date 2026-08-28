from __future__ import annotations

import os
import json
from pathlib import Path

import pytest

from p4_capture.artifacts import (
    CaptureStore,
    HardwareArtifactStore,
    P4ArtifactError,
    make_raw_artifact,
    make_raw_inventory,
    p2_inventory_from_raw_inventory,
)
from p4_capture.contracts import canonical_json, finalize_record, make_session_manifest, p2_snapshot_from_export
from p4_phase4_support import config_export
from spec.rtd_pilot_contracts import validate_inventory


def prepared_store(tmp_path: Path, *, capture_id: str = "capture:p4-artifact", session_id: str = "session:p4-artifact"):
    export = config_export(capture_id=capture_id, session_id=session_id)
    manifest = make_session_manifest(export, prepared_at="2026-08-16T00:00:00Z")
    snapshot = p2_snapshot_from_export(export)
    store = CaptureStore(tmp_path / "p4-test-only-capture-root")
    directory = store.prepare(manifest, export, snapshot)
    return store, directory, manifest


def test_test_only_raw_inventory_is_immutable_and_sealed_without_overwrite(tmp_path: Path) -> None:
    store, directory, manifest = prepared_store(tmp_path)
    source = tmp_path / "synthetic-test-only.trace"
    source.write_bytes(b"test_only synthetic raw bytes")

    raw = store.import_raw(directory, source)
    inventory = make_raw_inventory(manifest, [raw])
    inventory_path = store.write_inventory(directory, inventory)
    p2_inventory = p2_inventory_from_raw_inventory(inventory)

    assert raw["test_only"] is True
    assert raw["state"] == "available"
    assert inventory["test_only"] is True
    assert p2_inventory["artifacts"][0]["availability"] == "available"
    validate_inventory(p2_inventory)
    store.verify_inventory(directory)
    seal_path = store.seal(directory)

    assert inventory_path.exists()
    assert seal_path.exists()
    with pytest.raises(P4ArtifactError, match="sealed"):
        store.import_raw(directory, source)
    with pytest.raises(P4ArtifactError, match="already sealed"):
        store.seal(directory)


def test_duplicate_capture_session_identity_is_rejected(tmp_path: Path) -> None:
    store, _, _ = prepared_store(tmp_path, capture_id="capture:p4-duplicate", session_id="session:p4-one")
    export = config_export(capture_id="capture:p4-duplicate", session_id="session:p4-two")
    manifest = make_session_manifest(export, prepared_at="2026-08-16T00:00:00Z")

    with pytest.raises(P4ArtifactError, match="duplicate capture_id"):
        store.prepare(manifest, export, p2_snapshot_from_export(export))


def test_missing_partial_and_corrupt_raw_states_are_retained_and_fail_closed(tmp_path: Path) -> None:
    store, directory, manifest = prepared_store(tmp_path, capture_id="capture:p4-degraded", session_id="session:p4-degraded")
    source = tmp_path / "synthetic-test-only.trace"
    source.write_bytes(b"complete first")
    raw = store.import_raw(directory, source)
    partial = make_raw_artifact(
        capture_id=str(manifest["capture_id"]),
        state="partial",
        size=0,
        sha256_digest=None,
        logical_name="sidecars/collector.partial",
        kind="collector_sidecar",
    )
    assert partial["state"] == "partial"
    assert partial["sha256"] is None
    inventory = make_raw_inventory(manifest, [raw, partial])
    store.write_inventory(directory, inventory)
    failure_path = store.preserve_failure(directory, reason_code="TEST_ONLY_PARTIAL")
    assert failure_path.exists()

    raw_path = directory / raw["logical_name"]
    os.chmod(raw_path, 0o600)
    raw_path.write_bytes(b"changed after inventory")
    with pytest.raises(P4ArtifactError, match="hash/size mismatch"):
        store.seal(directory, seal_outcome="failed_preserved")
    assert not (directory / "seal.json").exists()


def test_missing_raw_projects_as_missing_and_repo_root_is_rejected(tmp_path: Path) -> None:
    store, directory, manifest = prepared_store(tmp_path, capture_id="capture:p4-missing", session_id="session:p4-missing")
    missing = make_raw_artifact(capture_id=str(manifest["capture_id"]), state="missing", size=0, sha256_digest=None)
    inventory = make_raw_inventory(manifest, [missing])
    store.write_inventory(directory, inventory)
    projected = p2_inventory_from_raw_inventory(inventory)
    assert projected["artifacts"][0]["availability"] == "missing"
    assert projected["artifacts"][0]["hash"] is None
    repository_root = Path(__file__).resolve().parents[2]
    with pytest.raises(P4ArtifactError, match="outside the repository"):
        CaptureStore(repository_root / "must-not-create-p4-captures")
    assert store.root.is_dir() or not store.root.exists()


def test_hardware_layout_is_external_identity_derived_and_no_case_layer(tmp_path: Path) -> None:
    root = tmp_path / "external-hardware"
    store = HardwareArtifactStore(root)
    export_a = config_export(capture_id="capture:p4-hw-a", session_id="session:p4-hw-a", test_only=False)
    export_b = config_export(capture_id="capture:p4-hw-a", session_id="session:p4-hw-b", test_only=False)
    manifest_a = make_session_manifest(export_a, prepared_at="2026-08-24T00:00:00Z")
    manifest_b = make_session_manifest(export_b, prepared_at="2026-08-24T00:00:00Z")
    first = store.prepare(manifest_a, export_a, board_id="board:A")
    second = store.prepare(manifest_b, export_b, board_id="board:B")
    assert first == root.resolve() / "board__A" / "session__p4-hw-a" / "hardware_smoke_noncase" / "capture__p4-hw-a"
    assert second != first and "case" not in first.relative_to(root).parts
    assert (first / "hardware_layout_manifest.json").is_file()
    with pytest.raises(P4ArtifactError, match="duplicate"):
        store.prepare(manifest_a, export_a, board_id="board:A")
    resolved, manifest, export, layout = store.resolve(board_id="board:A", session_id="session:p4-hw-a", capture_id="capture:p4-hw-a")
    assert resolved == first and manifest["case_id"] is None and export["test_only"] is False and layout["canonical_root"] == str(root.resolve())
    wrong_mode = json.loads((first / "hardware_layout_manifest.json").read_text(encoding="ascii"))
    wrong_mode["capture_mode"] = "test_only"
    (first / "hardware_layout_manifest.json").write_bytes(canonical_json(finalize_record(wrong_mode)))
    with pytest.raises(P4ArtifactError, match="identity mismatch"):
        store.resolve(board_id="board:A", session_id="session:p4-hw-a", capture_id="capture:p4-hw-a")


def test_hardware_layout_rejects_traversal_symlink_wrong_identity_case_and_test_only(tmp_path: Path) -> None:
    root = tmp_path / "external-hardware"
    store = HardwareArtifactStore(root)
    export = config_export(capture_id="capture:p4-hw", session_id="session:p4-hw", test_only=False)
    manifest = make_session_manifest(export, prepared_at="2026-08-24T00:00:00Z")
    target = store.prepare(manifest, export, board_id="board:unit")
    for board, session, capture in (("../escape", "session:p4-hw", "capture:p4-hw"), ("board:unit", "../escape", "capture:p4-hw"), ("board:unit", "session:p4-hw", "capture:../escape"), ("board:other", "session:p4-hw", "capture:p4-hw"), ("board:unit", "session:other", "capture:p4-hw")):
        with pytest.raises(P4ArtifactError):
            store.resolve(board_id=board, session_id=session, capture_id=capture)
    link = root / "board__unit" / "session__p4-hw" / "hardware_smoke_noncase"
    replacement = tmp_path / "outside"
    replacement.mkdir()
    link.rename(root / "moved-mode")
    link.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(P4ArtifactError, match="symlink"):
        store.resolve(board_id="board:unit", session_id="session:p4-hw", capture_id="capture:p4-hw")
    assert target.exists() is False
    with pytest.raises(P4ArtifactError, match="hardware_smoke"):
        HardwareArtifactStore(tmp_path / "separate").prepare(
            make_session_manifest(config_export(capture_id="capture:p4-test", session_id="session:p4-test"), prepared_at="x"),
            config_export(capture_id="capture:p4-test", session_id="session:p4-test"),
            board_id="board:unit",
        )
