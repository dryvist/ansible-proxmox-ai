"""The Hindsight lazy-dep gate: an exact pin becomes a minimum.

Upstream declares the same dependency twice with different operators. The
plugin asks for ``hindsight-client>=<min>`` and actively tries to upgrade an
older one; the lazy-dep table gates on ``hindsight-client==<exact>``. A current
client therefore satisfies upstream's own upgrade logic and fails upstream's
own gate, and the feature is reported unavailable while a working client sits
importable in the venv — with the fallback install path unable to run at all,
because the uv venv has no pip.

The patch rewrites only the OPERATOR, never the version, so an upstream version
bump still patches cleanly rather than silently matching nothing. That property
is the main thing these tests pin down.
"""

from __future__ import annotations

from conftest import _apply_runtime_patch


TASK = "Patch the Hindsight lazy-dep gate from an exact pin to a minimum"

# Verbatim shape of the upstream table, with neighbours either side so the
# pattern is exercised against a realistic block rather than a bare line.
PINNED_LAZY_DEPS_SOURCE = (
    "LAZY_DEPS = {\n"
    '    "memory.honcho": ("honcho-ai==1.2.3",),\n'
    '    "memory.hindsight": ("hindsight-client==0.6.1",),\n'
    '    "search.tavily": ("tavily-python>=0.5.0",),\n'
    "}\n"
)


def test_the_exact_pin_becomes_a_minimum() -> None:
    patched = _apply_runtime_patch(TASK, PINNED_LAZY_DEPS_SOURCE)
    assert '"memory.hindsight": ("hindsight-client>=0.6.1",),' in patched
    # The old form must be GONE, not merely shadowed: ansible.builtin.replace
    # reports ok when its pattern does not match, so "new text present" alone
    # would pass against a patch that never applied.
    assert '"hindsight-client==' not in patched


def test_only_the_hindsight_entry_is_touched() -> None:
    """Neighbouring exact pins must survive.

    A pattern loose enough to catch every `==` in the table would silently
    widen unrelated dependency gates, which is a much worse bug than the one
    being fixed — it would let genuinely incompatible versions load.
    """
    patched = _apply_runtime_patch(TASK, PINNED_LAZY_DEPS_SOURCE)
    assert '"memory.honcho": ("honcho-ai==1.2.3",),' in patched
    assert '"search.tavily": ("tavily-python>=0.5.0",),' in patched


def test_the_version_is_carried_across_not_hardcoded() -> None:
    """An upstream version bump must still patch.

    Distinguishes this design from one that rewrites the line to a literal
    ==0.6.1 -> >=0.6.1 substitution: that would stop matching the moment
    upstream moved the pin, and `replace` would report ok while doing nothing.
    """
    bumped = PINNED_LAZY_DEPS_SOURCE.replace("0.6.1", "0.9.2")
    patched = _apply_runtime_patch(TASK, bumped)
    assert '"memory.hindsight": ("hindsight-client>=0.9.2",),' in patched
    assert "0.6.1" not in patched


def test_the_patch_is_idempotent() -> None:
    """A second converge must not match again, or `replace` churns forever."""
    once = _apply_runtime_patch(TASK, PINNED_LAZY_DEPS_SOURCE)
    config = __import__("conftest")._replace_task(TASK)
    import re

    twice, count = re.subn(
        config["regexp"], config["replace"], once, flags=re.MULTILINE
    )
    assert count == 0, "the rewritten line still matches its own pattern"
    assert twice == once
