#!/usr/bin/env python3
"""Pull event messages from a configured queue and replay them to the local
webhook receiver.

Each message body is JSON {"event", "delivery", "headers", "body_b64"}. The
relay base64-decodes body_b64, signs those exact bytes with HMAC-SHA256 and
POSTs them to RELAY_TARGET_BASE/<route> with X-GitHub-Event, X-GitHub-Delivery
and X-Hub-Signature-256. The route is RELAY_ROUTE_PRIVATE when the payload's
repository.private is true, RELAY_ROUTE_PUBLIC when false; a payload without
that boolean is treated as malformed. A message is deleted only after a 2xx
response (which includes the receiver's `duplicate` answer); anything else,
including a malformed envelope, is left for the queue's redrive policy. One
JSON log line per message, plus a periodic stats line and an optional dead-man
ping.

Queue credentials are short-lived: the relay logs in to OpenBao with an
AppRole, reads RELAY_BAO_CREDS_PATH (an assumed-role credential endpoint),
revokes its OpenBao token, and repeats before the lease ends.
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
import urllib.parse
import urllib.request
from typing import Callable

REQUIRED = (
    "RELAY_QUEUE_SERVICE",
    "RELAY_QUEUE_URL",
    "RELAY_QUEUE_REGION",
    "RELAY_BAO_ADDR",
    "RELAY_BAO_ROLE_ID",
    "RELAY_BAO_SECRET_ID",
    "RELAY_BAO_CREDS_PATH",
    "RELAY_WEBHOOK_SECRET",
    "RELAY_TARGET_BASE",
    "RELAY_ROUTE_PUBLIC",
    "RELAY_ROUTE_PRIVATE",
)


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def checked_url(url: str) -> str:
    """Allow https anywhere and http only to the loopback receiver."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK):
        return url
    raise ValueError(f"refusing URL scheme/host: {parts.scheme}://{parts.hostname}")


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
    req = urllib.request.Request(checked_url(url), data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Event", event)
    req.add_header("X-GitHub-Delivery", delivery)
    req.add_header("X-Hub-Signature-256", sign(secret, raw))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(200).decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200).decode(errors="replace")
    except OSError as exc:
        return None, type(exc).__name__


def bao_post(url: str, body: dict | None, token: str | None = None) -> dict:
    """POST (or GET when body is None) to OpenBao and return the JSON reply."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(checked_url(url), data=data, method="GET" if body is None else "POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Vault-Token", token)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


class StsCredentials:
    """Short-lived queue credentials read from OpenBao, refreshed before expiry."""

    def __init__(self, cfg: dict, http: Callable[..., dict] = bao_post,
                 clock: Callable[[], float] = time.monotonic, margin: float = 300):
        self.cfg, self.http, self.clock, self.margin = cfg, http, clock, margin
        self.creds: dict[str, str] = {}
        self.expires = 0.0

    def needs_refresh(self) -> bool:
        return not self.creds or self.clock() >= self.expires - self.margin

    def refresh(self) -> dict[str, str]:
        addr = self.cfg["RELAY_BAO_ADDR"].rstrip("/")
        login = self.http(f"{addr}/v1/auth/approle/login",
                          {"role_id": self.cfg["RELAY_BAO_ROLE_ID"], "secret_id": self.cfg["RELAY_BAO_SECRET_ID"]})
        token = (login.get("auth") or {}).get("client_token")
        if not token:
            raise ValueError("AppRole login returned no client_token")
        try:
            reply = self.http(f"{addr}/v1/{self.cfg['RELAY_BAO_CREDS_PATH']}", None, token)
        finally:
            try:
                self.http(f"{addr}/v1/auth/token/revoke-self", {}, token)
            except OSError as exc:
                log(msg="revoke_failed", error=type(exc).__name__)
        data = reply.get("data") or {}
        session = data.get("session_token") or data.get("security_token")
        if not (data.get("access_key") and data.get("secret_key") and session):
            raise ValueError("credential reply is missing access_key/secret_key/session token")
        lease = int(reply.get("lease_duration") or 0)
        if lease <= self.margin:
            raise ValueError(f"credential lease {lease}s is not longer than the refresh margin")
        self.creds = {"aws_access_key_id": data["access_key"], "aws_secret_access_key": data["secret_key"],
                      "aws_session_token": session}
        self.expires = self.clock() + lease
        return self.creds


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
        urllib.request.urlopen(checked_url(url), timeout=10).close()
    except (OSError, ValueError) as exc:
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
    from botocore.exceptions import BotoCoreError, ClientError

    sts = StsCredentials(cfg)
    client = None
    counts = {"relayed": 0, "kept": 0, "max_age_s": 0}
    next_stats = time.monotonic()
    while True:
        try:
            if client is None or sts.needs_refresh():
                client = boto3.client(cfg["RELAY_QUEUE_SERVICE"], region_name=cfg["RELAY_QUEUE_REGION"],
                                      **sts.refresh())
                log(msg="credentials_refreshed")
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
        except (BotoCoreError, ClientError, OSError, ValueError) as exc:
            # Queue/OpenBao transport or reply errors: retry. Anything else crashes
            # and systemd restarts the unit.
            log(msg="poll_error", error=type(exc).__name__)
            client = None
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
