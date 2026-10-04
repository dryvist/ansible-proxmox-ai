#!/usr/bin/env python3
"""Append one complete innovation-log record under an exclusive file lock."""

from __future__ import annotations

import fcntl
import os
import sys


def append_record(path: str, record: bytes) -> bool:
    if not record or b"\n" in record or b"\r" in record:
        raise ValueError("record must be one non-empty line")

    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        existing = os.pread(fd, size, 0)
        if record in existing.splitlines():
            return False

        separator = b"\n" if existing and not existing.endswith(b"\n") else b""
        payload = separator + record + b"\n"
        written = os.write(fd, payload)
        if written != len(payload):
            os.ftruncate(fd, size)
            raise OSError("short innovation-log append")
        return True
    finally:
        os.close(fd)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: hermes-innovation-log-append.py LOG_PATH", file=sys.stderr)
        return 2

    record = sys.stdin.buffer.read()
    if record.endswith(b"\n"):
        record = record[:-1]
    try:
        appended = append_record(sys.argv[1], record)
    except (OSError, ValueError):
        print("innovation log append failed", file=sys.stderr)
        return 1

    print("appended" if appended else "already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
