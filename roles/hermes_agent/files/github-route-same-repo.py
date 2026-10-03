#!/usr/bin/env python3
"""Webhook script filter: pass a pull_request payload through unchanged only
when the head repository is the base repository; print nothing otherwise."""

import json
import sys


def same_repo(payload: dict) -> bool:
    try:
        head = payload["pull_request"]["head"]["repo"]["full_name"]
        base = payload["repository"]["full_name"]
    except (KeyError, TypeError):
        return False
    return bool(head) and head == base


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if isinstance(payload, dict) and same_repo(payload):
        json.dump(payload, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
