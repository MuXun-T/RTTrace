#!/usr/bin/env python3
"""Shared fail-closed helpers for Phase 1 observer utilities."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value

def load_board_contract(board_id: object, scripts_dir: Path) -> tuple[dict, Path]:
    contracts_path = scripts_dir.parent / "pinmaps" / "phase1_board_contracts.json"
    contracts = load_json(contracts_path)
    if contracts.get("schema_version") != "phase1-board-contracts-v1":
        raise ValueError("board contract schema mismatch")
    profiles = contracts.get("profiles")
    if not isinstance(board_id, str) or not isinstance(profiles, dict) or not isinstance(profiles.get(board_id), dict):
        raise ValueError("unknown board contract")
    contract = profiles[board_id]
    pinmap_name = contract.get("pinmap_file")
    if not isinstance(pinmap_name, str) or not pinmap_name:
        raise ValueError("board contract pinmap missing")
    pinmap_path = (contracts_path.parent / pinmap_name).resolve()
    if pinmap_path.parent != contracts_path.parent.resolve() or not pinmap_path.is_file():
        raise ValueError("board contract pinmap invalid")
    return contract, pinmap_path

def write_new_json(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, indent=2, sort_keys=True)
        target.write("\n")
