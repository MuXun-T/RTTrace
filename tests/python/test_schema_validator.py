from __future__ import annotations

import unittest

from spec.schema_validator import validate_schema


class SchemaValidatorTests(unittest.TestCase):
    def test_accepts_supported_subset_for_nested_objects_and_items(self) -> None:
        schema = {
            "type": "object",
            "required": ["name", "metrics", "tags"],
            "properties": {
                "name": {"type": "string"},
                "metrics": {
                    "type": "object",
                    "required": ["count"],
                    "properties": {
                        "count": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": {"type": "string"},
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        }

        reason = validate_schema(
            schema,
            {
                "name": "fixture",
                "metrics": {"count": 1, "note": "ok"},
                "tags": ["a", "b"],
            },
        )

        self.assertIsNone(reason)

    def test_reports_missing_required_property(self) -> None:
        reason = validate_schema(
            {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
            {},
        )

        self.assertEqual(reason, "missing required property 'name'")

    def test_reports_type_mismatch_for_union_type(self) -> None:
        reason = validate_schema({"type": ["string", "null"]}, 3)
        self.assertEqual(reason, "expected type string or null")

    def test_reports_enum_mismatch(self) -> None:
        reason = validate_schema({"type": "string", "enum": ["exact", "bounded"]}, "degraded")
        self.assertEqual(reason, "expected one of ['exact', 'bounded']")

    def test_reports_numeric_range_violation(self) -> None:
        below_min = validate_schema({"type": "integer", "minimum": 0}, -1)
        above_max = validate_schema({"type": "number", "maximum": 1}, 2)

        self.assertEqual(below_min, "below minimum 0")
        self.assertEqual(above_max, "above maximum 1")

    def test_reports_nested_property_path(self) -> None:
        reason = validate_schema(
            {
                "type": "object",
                "required": ["budget_vector"],
                "properties": {
                    "budget_vector": {
                        "type": "object",
                        "required": ["D_max"],
                        "properties": {"D_max": {"type": "integer"}},
                    }
                },
            },
            {"budget_vector": {"D_max": "1"}},
        )

        self.assertEqual(reason, "budget_vector.D_max: expected type integer")

    def test_reports_additional_properties_false(self) -> None:
        reason = validate_schema(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": False,
            },
            {"name": "ok", "extra": 1},
        )

        self.assertEqual(reason, "unexpected property 'extra'")

    def test_validates_additional_properties_schema(self) -> None:
        reason = validate_schema(
            {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            {"checksum": 1},
        )

        self.assertEqual(reason, "checksum: expected type string")

    def test_requires_exactly_one_oneof_branch(self) -> None:
        with self.subTest(case="zero-branch-match"):
            reason = validate_schema(
                {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                True,
            )
            self.assertEqual(reason, "oneOf matched 0 branches")

        with self.subTest(case="multiple-branch-match"):
            reason = validate_schema(
                {"oneOf": [{"type": "integer"}, {"type": "number"}]},
                3,
            )
            self.assertEqual(reason, "oneOf matched 2 branches")

    def test_rejects_bool_for_integer_and_number(self) -> None:
        integer_reason = validate_schema({"type": "integer"}, True)
        number_reason = validate_schema({"type": "number"}, False)

        self.assertEqual(integer_reason, "expected type integer")
        self.assertEqual(number_reason, "expected type number")


if __name__ == "__main__":
    unittest.main()
