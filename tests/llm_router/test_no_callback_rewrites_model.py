"""No router callback may change the requested model.

LiteLLM checks the key's model access against the request's `model` before
the pre-call hooks run. A hook that assigns a new `model` afterwards sends the
request to a group the key never held, and nothing re-checks it. The router's
own fallback walk re-checks the key on every hop, so routing changes belong
there, never in a callback.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CALLBACK_DIRS = [
    REPO_ROOT / "roles/llm_router/files/callbacks",
    REPO_ROOT / "roles/llm_router/templates/callbacks",
]
REWRITE = re.compile(
    r"""\[\s*["']model["']\s*\]\s*=(?!=)"""  # data["model"] = ...
    r"""|\.update\([^)]*(?:["']model["']\s*:|\bmodel\s*=)"""  # data.update(model=...)
    r"""|\.setdefault\(\s*["']model["']"""
)


def callback_sources():
    return sorted(p for d in CALLBACK_DIRS if d.is_dir() for p in d.iterdir() if p.suffix in {".py", ".j2"})


def test_pattern_catches_the_retired_rewrite():
    for line in ('data["model"] = ROLE_REDIRECT_TARGET', "data['model']=x", 'data.update({"model": x})',
                 "data.update(model=x)", 'data.setdefault("model", x)'):
        assert REWRITE.search(line), line
    assert not REWRITE.search('if data["model"] == x:')


def test_callbacks_exist():
    assert callback_sources(), "callback directory moved; update CALLBACK_DIRS"


def test_no_callback_assigns_model():
    hits = [
        f"{p.relative_to(REPO_ROOT)}:{n}: {line.strip()}"
        for p in callback_sources()
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if REWRITE.search(line)
    ]
    assert not hits, "callback rewrites the requested model:\n" + "\n".join(hits)
