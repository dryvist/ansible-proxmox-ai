"""A dedup baseline only works if the same job both reads and writes it.

Hermes suppresses repeats by recalling a memory key holding what it already
reported, filtering against it, and saving the updated fingerprint back. Every
part of that is load-bearing, and the failure mode when one part is missing is
the same in both directions and equally silent:

  - recall with no save — the key stays empty forever, so the filter always
    finds nothing to suppress and the job re-reports the same finding on every
    run. There is already a converge assertion for one instance of this
    (``splunk-digest-last``, in tasks/main.yml); this generalises it.
  - save with no recall — the job faithfully maintains a baseline it never
    reads, which looks like working dedup right up until you check the channel.

Neither raises anything. The job succeeds, the post goes out, and the only
symptom is the operator saying they cannot keep up with the noise.

Scope: the inline prompts defined in this role. Prompts that live in the pinned
``ai-llm-prompts`` catalog are out of scope here — they are not readable from
this repo — which is exactly why the catalog-sourced ``splunk-digest-last``
regression needed its own converge-time assertion.

Runs bare (``python3 tests/hermes_agent/test_memory_baseline_contract.py``) or
under pytest.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())

# A memory key as the prompts write it: a quoted kebab-case name ending -last,
# -cooldown or -ignore. Quoted because unquoted prose mentions of a job name
# would otherwise read as keys.
KEY = re.compile(r"[\"'`]([a-z0-9]+(?:-[a-z0-9]+)*-(?:last|cooldown|ignore))[\"'`]")

RECALL_VERBS = ("recall", "retrieval", "remembered", "read memory")
SAVE_VERBS = ("save", "record", "store", "write", "update")


def _inline_prompts() -> dict[str, str]:
    return {
        name: value
        for name, value in DEFAULTS.items()
        if name.startswith("hermes_agent_")
        and name.endswith(("_cron_prompt", "_card_prompt"))
        and isinstance(value, str)
    }


def _sentences_mentioning(prompt: str, key: str) -> list[str]:
    """Sentences of the prompt that name this key, lowercased."""
    return [s.lower() for s in re.split(r"(?<=[.!?])\s+", prompt) if key in s]


def _usage() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Which prompts recall each key, and which prompts save it."""
    recalled: dict[str, set[str]] = {}
    saved: dict[str, set[str]] = {}
    for name, prompt in _inline_prompts().items():
        for key in set(KEY.findall(prompt)):
            sentences = _sentences_mentioning(prompt, key)
            if any(v in s for s in sentences for v in RECALL_VERBS):
                recalled.setdefault(key, set()).add(name)
            if any(v in s for s in sentences for v in SAVE_VERBS):
                saved.setdefault(key, set()).add(name)
    return recalled, saved


def test_every_recalled_memory_key_is_written_by_something() -> None:
    """The dead-key trap, generalised.

    A prompt filtering against a key nothing writes is filtering against
    something permanently empty: the suppress branch cannot fire, so the job
    repeats itself forever while looking correctly written. That is the exact
    shape of the ``splunk-digest-last`` regression the converge assertion in
    tasks/main.yml guards, which reached production because the card that owned
    the key was removed while the recall stayed behind.

    Deliberately corpus-wide rather than per-prompt: reading ANOTHER job's
    baseline is legitimate and load-bearing here — the innovation card reads
    ``ai-news-last`` so its proposals follow what the scout actually found (see
    test_the_innovation_card_reads_the_scouts_finds). What must never happen is
    a key no prompt anywhere writes.
    """
    recalled, saved = _usage()
    orphans = {
        key: sorted(readers) for key, readers in recalled.items() if key not in saved
    }
    assert not orphans, "\n".join(
        f"{key!r} is recalled by {readers} but written by nothing" for key, readers in orphans.items()
    )


def test_every_written_memory_key_is_read_by_something() -> None:
    """The mirror failure: a baseline maintained and never consulted looks
    exactly like working dedup until you read the channel."""
    recalled, saved = _usage()
    unread = {key: sorted(writers) for key, writers in saved.items() if key not in recalled}
    assert not unread, "\n".join(
        f"{key!r} is written by {writers} but read by nothing" for key, writers in unread.items()
    )


def test_the_high_cadence_cards_all_carry_a_baseline() -> None:
    """A card that runs many times a day and holds no baseline cannot avoid
    repeating itself — cadence is what turns a missing baseline from a latent
    flaw into the operator's actual complaint.

    Scoped to cards whose prompt lives in this repo AND that are not paused; a
    paused card emits nothing, and a catalog prompt is not readable here.
    """
    paused = set(DEFAULTS["hermes_agent_kanban_paused_jobs"])
    inline = _inline_prompts()
    offenders = []
    for card in DEFAULTS["hermes_agent_kanban_cards"]:
        if card["job"] in paused:
            continue
        if int(card.get("interval_hours", 24)) > 8:
            continue
        prompt = inline.get(card["prompt_var"])
        if prompt is None:  # catalog-sourced, asserted at converge time instead
            continue
        if not KEY.search(prompt):
            offenders.append(
                f"{card['title']} runs every {card['interval_hours']}h with no memory baseline"
            )
    assert not offenders, "\n".join(offenders)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
