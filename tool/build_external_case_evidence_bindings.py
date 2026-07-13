#!/usr/bin/env python3
"""Build or verify one closed P7.2-to-P7.3-to-P7.4 binding."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

from parser.external_case_evidence_binding import ROOT, binding_bytes, build_case_binding, verify_case_binding
from parser.external_case_evidence_binding_models import ExternalCaseEvidenceBinding
from parser.external_case_package_evidence import case_package_specs


CASE_IDS = tuple(spec.case_id for spec in case_package_specs())


def _output_path(value: str) -> Path:
    path = Path(os.path.abspath(value))
    try:
        path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise ValueError("binding output must be outside the repository")
    for parent in (path.parent, *path.parents):
        info = os.lstat(parent)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("binding output parent must be a non-symlink directory")
        if parent == parent.parent:
            break
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise ValueError("binding output must not be a symlink")
        raise ValueError("binding output must not already exist")
    except FileNotFoundError:
        return path


def _read_binding_input(value: str) -> bytes:
    fd = os.open(Path(value), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("binding input must be a non-linked regular file")
        raw = b"".join(iter(lambda: os.read(fd, 65536), b""))
        after = os.fstat(fd)
        if len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_nlink) != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_nlink):
            raise ValueError("binding input changed while read")
        return raw
    finally:
        os.close(fd)


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--case", required=True, choices=CASE_IDS)
    build.add_argument("--output")
    verify = commands.add_parser("verify")
    verify.add_argument("--input", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            spec = next(item for item in case_package_specs() if item.case_id == args.case)
            data = binding_bytes(build_case_binding(spec))
            if args.output is None:
                sys.stdout.buffer.write(data)
            else:
                _write_new(_output_path(args.output), data)
        else:
            value = ExternalCaseEvidenceBinding.from_dict(json.loads(_read_binding_input(args.input).decode("utf-8")))
            verify_case_binding(value)
    except (OSError, ValueError, json.JSONDecodeError):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
