from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

ASSET_ROOT = Path(__file__).resolve().parent / "assets"
SCHEMA_ROOT = ASSET_ROOT / "schema"


def load_default_dictionary() -> dict[str, Any]:
    return json_load(ASSET_ROOT / "dictionary.json")


def load_schema(name: str) -> dict[str, Any]:
    return json_load(SCHEMA_ROOT / name)


def json_load(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def json_dump(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(serialize(data), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def jsonl_load(path: str | Path) -> list[dict[str, Any]]:
    return list(jsonl_iter(path))


def jsonl_iter(path: str | Path) -> Iterator[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("jsonl row must be an object")
            yield payload


def jsonl_dump(path: str | Path, rows: list[Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(serialize(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def checksum_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def checksum_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def serialize(data: Any) -> Any:
    if dataclasses.is_dataclass(data):
        return serialize(dataclasses.asdict(data))
    if isinstance(data, Path):
        return str(data)
    if isinstance(data, dict):
        return {str(key): serialize(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [serialize(item) for item in data]
    return data
