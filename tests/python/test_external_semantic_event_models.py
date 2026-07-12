from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from parser.external_semantic_event_models import EventKind, ReplayReason, ReplayReport, ReplayState, SemanticEvent, canonical_json, ordered_reasons
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema

ROOT = Path(__file__).resolve().parents[2]


def event() -> SemanticEvent:
    return SemanticEvent(0, 1, "us", "btf", 0, "0001", "Runner", EventKind.MARKER, None, None, None, None, None, None, (("action", "create"),), 0)


class SemanticEventModelTests(unittest.TestCase):
    def test_event_is_closed_immutable_and_canonical(self) -> None:
        value = event()
        self.assertEqual(SemanticEvent.from_dict(value.to_dict()), value)
        self.assertEqual(canonical_json(value.to_dict()), canonical_json(value.to_dict()))
        with self.assertRaises(FrozenInstanceError): value.timestamp = 2  # type: ignore[misc]
        bad = value.to_dict(); bad["extra"] = True
        with self.assertRaises(ValueError): SemanticEvent.from_dict(bad)

    def test_limits_and_reason_priority(self) -> None:
        with self.assertRaises(ValueError): SemanticEvent(0, 1000001, "us", "btf", None, None, None, EventKind.MARKER, None, None, None, None, None, None, (), 0)
        self.assertEqual(ordered_reasons((ReplayReason.PARSE_ERROR, ReplayReason.SOURCE_MUTATED, ReplayReason.PARSE_ERROR)), (ReplayReason.SOURCE_MUTATED, ReplayReason.PARSE_ERROR))

    def test_replay_state_rules(self) -> None:
        report = ReplayReport(ReplayState.REPLAY_FAIL, True, False, ReplayReason.PARSE_ERROR, (ReplayReason.PARSE_ERROR,), "opened", "p", "c", "a" * 64, "b" * 64, "c" * 64, "d" * 64, None, 0, 0, 0)
        self.assertEqual(report.to_dict()["replay_state"], "replay_fail")
        with self.assertRaises(ValueError): ReplayReport(ReplayState.REPLAY_PASS, True, True, ReplayReason.PARSE_ERROR, (ReplayReason.PARSE_ERROR,), "opened", "p", "c", "a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64, 0, 0, 0)

    def test_schema_mirrors_and_event_schema(self) -> None:
        for name in ("external_semantic_event.schema.json", "external_comparison_profile.schema.json", "external_replay_report.schema.json"):
            self.assertEqual((ROOT / "spec/schema" / name).read_bytes(), (ROOT / "spec/assets/schema" / name).read_bytes())
        self.assertIsNone(validate_schema(load_schema("external_semantic_event.schema.json"), event().to_dict()))
        self.assertEqual(hashlib.sha256(canonical_json(event().to_dict())).hexdigest(), hashlib.sha256(canonical_json(event().to_dict())).hexdigest())


if __name__ == "__main__": unittest.main()
