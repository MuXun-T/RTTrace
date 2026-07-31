from __future__ import annotations

import re
from typing import Any


def validate_schema(schema: dict[str, Any], value: Any) -> str | None:
    return _validate(schema, value, path="")


def _validate(schema: dict[str, Any], value: Any, path: str) -> str | None:
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        match_count = 0
        for candidate in one_of:
            if isinstance(candidate, dict) and _validate(candidate, value, path="") is None:
                match_count += 1
        if match_count != 1:
            return _format_reason(path, f"oneOf matched {match_count} branches")

    schema_type = schema.get("type")
    if schema_type is not None:
        type_options = [schema_type] if isinstance(schema_type, str) else list(schema_type)
        if not any(_matches_type(option, value) for option in type_options):
            return _format_reason(path, f"expected type {_format_type_options(type_options)}")

    if "enum" in schema:
        enum_values = list(schema.get("enum") or [])
        if not any(_json_equal(value, candidate) for candidate in enum_values):
            return _format_reason(path, f"expected one of {enum_values!r}")

    if "pattern" in schema and isinstance(value, str):
        pattern = schema["pattern"]
        if not isinstance(pattern, str) or re.search(pattern, value) is None:
            return _format_reason(path, f"does not match pattern {pattern!r}")

    if _is_number_instance(value):
        if "minimum" in schema and float(value) < float(schema["minimum"]):
            return _format_reason(path, f"below minimum {schema['minimum']!r}")
        if "maximum" in schema and float(value) > float(schema["maximum"]):
            return _format_reason(path, f"above maximum {schema['maximum']!r}")

    if isinstance(value, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                return _format_reason(path, f"missing required property {key!r}")

        properties = schema.get("properties")
        property_schemas = properties if isinstance(properties, dict) else {}
        for key, child_schema in property_schemas.items():
            if key not in value or not isinstance(child_schema, dict):
                continue
            reason = _validate(child_schema, value[key], path=_join_path(path, key))
            if reason is not None:
                return reason

        additional = schema.get("additionalProperties", True)
        extras = sorted(key for key in value if key not in property_schemas)
        if additional is False and extras:
            return _format_reason(path, f"unexpected property {extras[0]!r}")
        if isinstance(additional, dict):
            for key in extras:
                reason = _validate(additional, value[key], path=_join_path(path, key))
                if reason is not None:
                    return reason

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                reason = _validate(item_schema, item, path=_join_index(path, index))
                if reason is not None:
                    return reason

    return None


def _matches_type(type_name: Any, value: Any) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return _is_number_instance(value)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    return False


def _is_number_instance(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_type_options(type_options: list[Any]) -> str:
    return " or ".join(str(option) for option in type_options)


def _join_path(path: str, key: str) -> str:
    if not path:
        return key
    return f"{path}.{key}"


def _join_index(path: str, index: int) -> str:
    if not path:
        return f"[{index}]"
    return f"{path}[{index}]"


def _format_reason(path: str, message: str) -> str:
    if not path:
        return message
    return f"{path}: {message}"


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(lhs, rhs) for lhs, rhs in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left.keys()) == set(right.keys()) and all(_json_equal(left[key], right[key]) for key in left)
    return left == right
