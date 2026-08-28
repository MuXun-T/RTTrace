from __future__ import annotations

import json
from pathlib import Path


def test_all_p4_schema_files_are_valid_and_closed() -> None:
    schema_root = Path(__file__).parents[2] / "p4_capture" / "schema"
    schemas = sorted(schema_root.glob("*.schema.json"))
    assert len(schemas) >= 9
    for path in schemas:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["type"] == "object"
        assert isinstance(schema["additionalProperties"], bool)
        assert "record_digest" in schema["required"]
