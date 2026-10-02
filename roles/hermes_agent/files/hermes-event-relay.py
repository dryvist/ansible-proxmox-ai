#!/usr/bin/env python3
"""Pull event messages from a configured queue and replay them to the local
webhook receiver.

Each message body is JSON {"event", "delivery", "headers", "body_b64"}. The
relay base64-decodes body_b64, signs those exact bytes with HMAC-SHA256 and
POSTs them to RELAY_TARGET_BASE/<route> with X-GitHub-Event, X-GitHub-Delivery
and X-Hub-Signature-256. The route is RELAY_ROUTE_PRIVATE when the payload's
repository.private is true, RELAY_ROUTE_PUBLIC when false; a payload without
that boolean is treated as malformed. A message is deleted only after a 2xx response (which
includes the receiver's `duplicate` answer); anything else, including a
malformed envelope, is left for the queue's redrive policy. One JSON log line
per message, plus a periodic stats line and an optional dead-man ping.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

REQUIRED = (
    "RELAY_QUEUE_SERVICE",
    "RELAY_QUEUE_URL",
    "RELAY_QUEUE_REGION",
    "RELAY_ACCESS_KEY_ID",
    "RELAY_SECRET_ACCESS_KEY",
    "RELAY_WEBHOOK_SECRET",
    "RELAY_TARGET_BASE",
    "RELAY_ROUTE_PUBLIC",
    "RELAY_ROUTE_PRIVATE",
)


def _field(env: dict, name: str) -> str:
    val = env.get(name)
    if not isinstance(val, str) or not val:
        raise ValueError(f"envelope field {name} missing")
    return val


def parse_envelope(text: str) -> tuple[str, str, bytes]:
    """Return (event, delivery, raw_body). Raises ValueError when malformed."""
    try:
        env = json.loads(text)
    except ValueError as exc:
        raise ValueError("envelope is not JSON") from exc
    if not isinstance(env, dict):
        raise ValueError("envelope is not an object")
    event = _field(env, "event")
    delivery = _field(env, "delivery")
    b64 = _field(env, "body_b64")
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("body_b64 is not valid base64") from exc
    return event, delivery, raw


def target_url(raw: bytes, cfg: dict) -> str:
    """Pick the webhook route from the payload's repository.private."""
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ValueError("body is not JSON") from exc
    repo = payload.get("repository") if isinstance(payload, dict) else None
    private = repo.get("private") if isinstance(repo, dict) else None
    if not isinstance(private, bool):
        raise ValueError("repository.private missing")
    route = cfg["RELAY_ROUTE_PRIVATE"] if private else cfg["RELAY_ROUTE_PUBLIC"]
    return f"{cfg['RELAY_TARGET_BASE'].rstrip('/')}/{route}"


def sign(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def should_delete(status: int | None) -> bool:
    """2xx covers accepted, coalesced, ignored-by-filter and duplicate answers."""
    return status is not None and 200 <= status < 300


def forward(url: str, secret: str, event: str, delivery: str, raw: bytes) -> tuple[int | None, str]:
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Event", event)
    req.add_header("X-GitHub-Delivery", delivery)
    req.add_header("X-Hub-Signature-256", sign(secret, raw))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - local receiver from config
            return resp.status, resp.read(200).decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200).decode(errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, type(exc).__name__


def log(**fields) -> None:
    print(json.dumps({"ts": int(time.time()), **fields}, sort_keys=True), flush=True)


def handle(msg: dict, cfg: dict, post=forward) -> bool:
    """Process one queue message; return True when it should be deleted."""
    receives = msg.get("Attributes", {}).get("ApproximateReceiveCount")
    try:
        event, delivery, raw = parse_envelope(msg.get("Body", ""))
        url = target_url(raw, cfg)
    except ValueError as exc:
        log(msg="poison", id=msg.get("MessageId"), error=str(exc), receives=receives)
        return False
    status, detail = post(url, cfg["RELAY_WEBHOOK_SECRET"], event, delivery, raw)
    delete = should_delete(status)
    log(msg="relayed", route=url.rsplit("/", 1)[1], event=event, delivery=delivery, status=status, deleted=delete,
        receives=receives, detail=None if delete else detail)
    return delete


def depth(client, url: str) -> int | None:
    if not url:
        return None
    attrs = client.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])
    return int(attrs["Attributes"]["ApproximateNumberOfMessages"])


def ping(url: str) -> None:
    if not url:
        return
    try:
        urllib.request.urlopen(url, timeout=10).close()  # noqa: S310 - dead-man URL from config
    except (urllib.error.URLError, TimeoutError) as exc:
        log(msg="ping_failed", error=type(exc).__name__)


def main() -> int:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print(f"hermes-event-relay: missing env: {' '.join(missing)}", file=sys.stderr)
        return 2
    cfg = {k: os.environ[k] for k in REQUIRED}
    dlq_url = os.environ.get("RELAY_DLQ_URL", "")
    hc_url = os.environ.get("RELAY_HEALTHCHECK_URL", "")
    stats_every = int(os.environ.get("RELAY_STATS_SECONDS", "300"))

    import boto3  # deferred: the pure helpers above are tested without it

    client = boto3.client(
        cfg["RELAY_QUEUE_SERVICE"],
        region_name=cfg["RELAY_QUEUE_REGION"],
        aws_access_key_id=cfg["RELAY_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["RELAY_SECRET_ACCESS_KEY"],
    )
    counts = {"relayed": 0, "kept": 0, "max_age_s": 0}
    next_stats = time.monotonic()
    while True:
        try:
            resp = client.receive_message(
                QueueUrl=cfg["RELAY_QUEUE_URL"],
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,
                MessageSystemAttributeNames=["ApproximateReceiveCount", "SentTimestamp"],
            )
            for msg in resp.get("Messages", []):
                sent = int(msg.get("Attributes", {}).get("SentTimestamp", "0")) / 1000
                if sent:
                    counts["max_age_s"] = max(counts["max_age_s"], int(time.time() - sent))
                if handle(msg, cfg):
                    client.delete_message(QueueUrl=cfg["RELAY_QUEUE_URL"], ReceiptHandle=msg["ReceiptHandle"])
                    counts["relayed"] += 1
                else:
                    counts["kept"] += 1
            if time.monotonic() >= next_stats:
                log(msg="stats", queue_depth=depth(client, cfg["RELAY_QUEUE_URL"]),
                    dead_letter_depth=depth(client, dlq_url), **counts)
                ping(hc_url)
                counts = {"relayed": 0, "kept": 0, "max_age_s": 0}
                next_stats = time.monotonic() + stats_every
        except Exception as exc:  # noqa: BLE001 - keep polling; systemd restarts only on crash
            log(msg="poll_error", error=type(exc).__name__)
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
