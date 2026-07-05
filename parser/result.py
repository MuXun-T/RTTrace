from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from .models import UntrustedWindow

T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    code: str
    message: str = ""
    data: T | None = None
    warnings: list[str] = field(default_factory=list)
    untrusted_windows: list["UntrustedWindow"] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code == "OK"


def ok_result(
    data: T,
    warnings: list[str] | None = None,
    untrusted_windows: list["UntrustedWindow"] | None = None,
) -> Result[T]:
    return Result(
        code="OK",
        data=data,
        warnings=warnings or [],
        untrusted_windows=untrusted_windows or [],
    )


def err_result(
    code: str,
    message: str,
    warnings: list[str] | None = None,
    untrusted_windows: list["UntrustedWindow"] | None = None,
) -> Result[None]:
    return Result(
        code=code,
        message=message,
        warnings=warnings or [],
        untrusted_windows=untrusted_windows or [],
    )
