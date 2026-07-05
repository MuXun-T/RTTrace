from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    code: str
    message: str = ""
    data: T | None = None
    warnings: list[str] = field(default_factory=list)
    untrusted_windows: list[Any] = field(default_factory=list)

    @classmethod
    def ok(
        cls,
        data: T | None = None,
        *,
        message: str = "",
        warnings: list[str] | None = None,
        untrusted_windows: list[Any] | None = None,
    ) -> "Result[T]":
        return cls(
            code="OK",
            message=message,
            data=data,
            warnings=list(warnings or []),
            untrusted_windows=list(untrusted_windows or []),
        )

    @classmethod
    def err(
        cls,
        code: str,
        message: str,
        *,
        data: T | None = None,
        warnings: list[str] | None = None,
        untrusted_windows: list[Any] | None = None,
    ) -> "Result[T]":
        return cls(
            code=code,
            message=message,
            data=data,
            warnings=list(warnings or []),
            untrusted_windows=list(untrusted_windows or []),
        )

    @property
    def is_ok(self) -> bool:
        return self.code == "OK"

    def require(self) -> T:
        if not self.is_ok:
            raise RuntimeError(f"{self.code}: {self.message}")
        return self.data  # type: ignore[return-value]
