"""Concurrent writer regression coverage for the Hermes innovation log."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "roles/hermes_agent/files/hermes-innovation-log-append.py"


def _legacy_rewrite(path: Path, record: bytes, both_read: Barrier) -> None:
    snapshot = path.read_bytes()
    both_read.wait(timeout=5)
    path.write_bytes(snapshot + record + b"\n")


def _locked_append(path: Path, record: bytes, start: Barrier) -> None:
    start.wait(timeout=5)
    subprocess.run(
        [sys.executable, str(HELPER), str(path)],
        input=record + b"\n",
        check=True,
        capture_output=True,
        timeout=5,
    )


class InnovationLogAppendTest(unittest.TestCase):
    def test_legacy_read_modify_write_reproduction_loses_one_record(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "innovation-log.md"
            path.write_bytes(b"")
            records = (b"2026-10-04|writer-a|fp-a", b"2026-10-04|writer-b|fp-b")
            both_read = Barrier(2)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_legacy_rewrite, path, record, both_read) for record in records]
                for future in futures:
                    future.result()

            actual = set(path.read_bytes().splitlines())
            self.assertEqual(len(actual & set(records)), 1)

    def test_two_locked_writers_preserve_both_complete_records(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "innovation-log.md"
            records = (b"2026-10-04|writer-a|fp-a", b"2026-10-04|writer-b|fp-b")
            start = Barrier(2)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_locked_append, path, record, start) for record in records]
                for future in futures:
                    future.result()

            self.assertEqual(set(path.read_bytes().splitlines()), set(records))

    def test_duplicate_record_is_not_appended_twice(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "innovation-log.md"
            record = b"2026-10-04|writer-a|fp-a"
            for _ in range(2):
                subprocess.run(
                    [sys.executable, str(HELPER), str(path)],
                    input=record + b"\n",
                    check=True,
                    capture_output=True,
                    timeout=5,
                )

            self.assertEqual(path.read_bytes().splitlines(), [record])


if __name__ == "__main__":
    unittest.main()
