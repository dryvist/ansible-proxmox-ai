#!/usr/bin/env python3
"""Verify pinned Hugging Face local-dir downloads without Hub requests."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

_REVISION = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_SHA1 = re.compile(r"[0-9a-f]{40}")


def _safe_repo_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if "\\" in value or path.is_absolute() or not path.parts or ".." in path.parts or ".cache" in path.parts:
        raise ValueError("invalid model-store file path")
    return path


def _read_metadata_file(path: Path, relative_path: str) -> tuple[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) < 3 or not lines[0] or not lines[1]:
            raise ValueError
        float(lines[2])
    except (OSError, ValueError) as error:
        raise ValueError(f"download metadata is invalid for {relative_path}") from error
    return lines[0], lines[1].lower()


def _digest(path: Path, etag: str) -> str:
    if _SHA256.fullmatch(etag):
        digest = hashlib.sha256()
    elif _GIT_SHA1.fullmatch(etag):
        digest = hashlib.sha1()
        digest.update(f"blob {path.stat().st_size}\0".encode("ascii"))
    else:
        raise ValueError("download metadata has an unsupported checksum")

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _downloaded_files(root: Path, include_globs: list[str]) -> dict[str, tuple[str, str]]:
    metadata_root = root / ".cache" / "huggingface" / "download"
    if not metadata_root.is_dir():
        raise ValueError("pinned Hugging Face download metadata is missing")

    files: dict[str, tuple[str, str]] = {}
    for metadata_path in metadata_root.rglob("*.metadata"):
        relative_path = metadata_path.relative_to(metadata_root).as_posix()[: -len(".metadata")]
        safe_path = _safe_repo_path(relative_path)
        if not any(fnmatch.fnmatchcase(safe_path.as_posix(), pattern) for pattern in include_globs):
            continue
        files[safe_path.as_posix()] = _read_metadata_file(metadata_path, safe_path.as_posix())
    return files


def _verify(root: Path, revision: str, include_globs: list[str]) -> int:
    if not include_globs or any(not isinstance(pattern, str) or not pattern for pattern in include_globs):
        raise ValueError("registered model-store include globs are required")

    downloaded = _downloaded_files(root, include_globs)
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if (path.is_file() or path.is_symlink()) and ".cache" not in path.relative_to(root).parts
    }
    selected: dict[str, str] = {}
    for pattern in include_globs:
        matches = {
            path for path in actual_files | downloaded.keys()
            if fnmatch.fnmatchcase(path, pattern)
        }
        if not matches:
            raise ValueError(f"pinned model-store include has no downloaded files: {pattern}")
        for path in matches:
            if path not in downloaded:
                raise ValueError(f"download metadata is missing for {path}")
            downloaded_revision, etag = downloaded[path]
            if downloaded_revision != revision:
                raise ValueError(f"download metadata revision differs for {path}")
            selected[path] = etag

    for relative_path, etag in selected.items():
        safe_path = _safe_repo_path(relative_path)
        path = root.joinpath(*safe_path.parts)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"pinned model-store file is missing: {safe_path.as_posix()}")
        if _digest(path, etag) != etag:
            raise ValueError(f"pinned model-store checksum differs for {safe_path.as_posix()}")
    return len(selected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--include-globs", required=True)
    arguments = parser.parse_args(argv)

    try:
        if not _REVISION.fullmatch(arguments.revision):
            raise ValueError("registry revision must be a pinned commit hash")
        if not arguments.root.is_dir():
            raise ValueError("model-store directory is missing")
        include_globs = json.loads(arguments.include_globs)
        if not isinstance(include_globs, list):
            raise ValueError("registered model-store include globs must be a list")
        checked_count = _verify(arguments.root, arguments.revision, include_globs)
        print(f"Verified {checked_count} pinned local model-store file(s)")
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"Local model-store verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
