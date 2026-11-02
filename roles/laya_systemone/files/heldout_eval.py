#!/usr/bin/env python3
"""Score held-out CSV rows through Laya without writing row-level results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_BATCH_BODY_BYTES = 2 * 1024 * 1024
MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class EvaluationError(Exception):
    """A safe error message that never includes request text."""


def read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or "text" not in reader.fieldnames or "label" not in reader.fieldnames:
            raise EvaluationError("CSV must contain text and label columns")
        rows = []
        for row in reader:
            text = row.get("text")
            label = row.get("label")
            if text is None or label is None:
                raise EvaluationError("CSV contains a row with a missing text or label value")
            rows.append({"text": text, "label": label.strip()})

    if not rows:
        raise EvaluationError("CSV has no rows")
    return rows, distinct_labels(rows)


def distinct_labels(rows: list[dict[str, str]]) -> list[str]:
    labels = []
    seen = set()
    for row in rows:
        label = row["label"].strip()
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    if len(labels) < 2:
        raise EvaluationError("CSV must contain at least two distinct non-empty labels")
    return labels


def encoded_body(model: str, states: list[str], questions: dict) -> bytes:
    return json.dumps(
        {"model": model, "states": states, "questions": questions},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def batches(rows: list[dict[str, str]], model: str, questions: dict, batch_size: int):
    current = []
    for row in rows:
        candidate = current + [row["text"]]
        if current and (
            len(candidate) > batch_size
            or len(encoded_body(model, candidate, questions)) > MAX_BATCH_BODY_BYTES
        ):
            yield current
            current = [row["text"]]
        else:
            current = candidate

        if len(encoded_body(model, current, questions)) > MAX_BATCH_BODY_BYTES:
            raise EvaluationError("one state exceeds the Laya batch request size limit")

    if current:
        yield current


def post_batch(url: str, api_key: str, model: str, states: list[str], questions: dict) -> list[dict]:
    request = Request(
        url,
        data=encoded_body(model, states, questions),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(4):
        try:
            with urlopen(request, timeout=60) as response:
                if response.status != 200:
                    raise EvaluationError(f"Laya returned HTTP {response.status} for model {model}")
                try:
                    payload = json.loads(response.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise EvaluationError("Laya returned an invalid JSON response") from exc
            results = payload.get("results") if isinstance(payload, dict) else payload
            if not isinstance(results, list) or len(results) != len(states):
                raise EvaluationError("Laya returned an unexpected batch response shape")
            if not all(isinstance(item, dict) for item in results):
                raise EvaluationError("Laya returned an unexpected batch result")
            return results
        except HTTPError as exc:
            if exc.code in (429, 503) and attempt < 3:
                time.sleep(2**attempt)
                continue
            raise EvaluationError(f"Laya returned HTTP {exc.code} for model {model}") from exc
        except URLError as exc:
            raise EvaluationError("could not reach the Laya batch endpoint") from exc

    raise EvaluationError(f"Laya returned a retryable error for model {model} after four tries")


def score_model(
    rows: list[dict[str, str]], model: str, questions: dict, url: str, api_key: str, batch_size: int
) -> dict:
    correct = 0
    unanswered = 0
    confidence_values = []
    per_class = {}
    for row in rows:
        per_class.setdefault(row["label"], {"n": 0, "correct": 0})["n"] += 1

    for state_batch, start in _indexed_batches(rows, model, questions, batch_size):
        results = post_batch(url, api_key, model, state_batch, questions)
        for row, result in zip(rows[start : start + len(state_batch)], results):
            answers = result.get("answers")
            answer = answers.get("label") if isinstance(answers, dict) else None
            answer = answer if isinstance(answer, dict) else {}
            choice = answer.get("choice")
            if choice is None:
                unanswered += 1
            elif choice == row["label"]:
                correct += 1
                per_class[row["label"]]["correct"] += 1

            confidence = answer.get("answer_confidence", answer.get("confidence"))
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(confidence):
                confidence_values.append(float(confidence))

    return {
        "accuracy": correct / len(rows),
        "correct": correct,
        "unanswered": unanswered,
        "mean_answer_confidence": (
            round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else None
        ),
        "per_class": per_class,
    }


def _indexed_batches(rows: list[dict[str, str]], model: str, questions: dict, batch_size: int):
    start = 0
    for state_batch in batches(rows, model, questions, batch_size):
        yield state_batch, start
        start += len(state_batch)


def write_atomic(path: Path, result: dict) -> None:
    encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def evaluate(args: argparse.Namespace) -> dict:
    if not 1 <= args.batch_size <= 64:
        raise EvaluationError("batch size must be between 1 and 64")
    if not args.models or not all(MODEL_NAME.fullmatch(model) for model in args.models):
        raise EvaluationError("at least one safe model name is required")

    rows, heldout_labels = read_rows(args.csv)
    labels = read_rows(args.train_csv)[1] if args.train_csv else heldout_labels
    questions = {
        "label": {
            "type": "choice",
            "instructions": "Classify the input into the correct category.",
            "criteria": labels,
        }
    }

    api_key = os.environ.get("LAYA_API_KEY", "")
    if not api_key:
        raise EvaluationError("LAYA_API_KEY is required")

    models = {
        model: score_model(rows, model, questions, args.url, api_key, args.batch_size)
        for model in args.models
    }
    digest = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    return {
        "heldout_sha256": digest,
        "n": len(rows),
        "labels": labels,
        "laya_url_path": "/v1/systemone/batch",
        "models": models,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--train-csv", type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", dest="models", action="append", required=True)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        write_atomic(args.out, evaluate(args))
    except (EvaluationError, OSError, csv.Error, UnicodeError) as exc:
        print(f"held-out evaluation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
