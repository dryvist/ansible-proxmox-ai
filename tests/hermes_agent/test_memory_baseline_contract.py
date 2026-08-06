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


def _expand_field(field: str, span: int) -> set[int]:
    """Values a single cron field (comma list of `*`, `*/N`, `H`, `H1-H2`) fires on."""
    out: set[int] = set()
    for part in field.split(","):
        if part == "*":
            out.update(range(span))
        elif part.startswith("*/"):
            out.update(range(0, span, int(part[2:])))
        elif "-" in part:
            lo, hi = part.split("-")
            out.update(range(int(lo), int(hi) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def _fires_per_day(schedule: str) -> int:
    """Day-of-month/month/weekday restrictions are ignored: every current
    weekday-restricted schedule fires at a single fixed hour:minute (docs-sync,
    fleet-health), so this is a safe over-approximation, not an undercount."""
    minute_field, hour_field = schedule.split()[:2]
    return len(_expand_field(minute_field, 60)) * len(_expand_field(hour_field, 24))


def test_the_high_cadence_jobs_all_carry_a_baseline() -> None:
    """A job that runs many times a day and holds no baseline cannot avoid
    repeating itself — cadence is what turns a missing baseline from a latent
    flaw into the operator's actual complaint.

    Native-cron reframe: there is no `interval_hours` field any more — cadence
    is read directly off the crontab `schedule` (>=3 fires/day, i.e. no gap
    wider than ~8h, the same cutoff the old field used). Scoped to jobs whose
    prompt lives in this repo (`prompt_var`) AND that are actually reconciled
    (`enabled` is not the literal `False` the retired -v2 entries carry); a
    catalog-sourced `prompt_file` job is not readable here.
    """
    inline = _inline_prompts()
    offenders = []
    for job in DEFAULTS["hermes_agent_direct_cron_jobs"] + DEFAULTS["hermes_agent_kanban_cards"]:
        if job.get("enabled") is False:
            continue
        if _fires_per_day(job["schedule"]) < 3:
            continue
        prompt_var = job.get("prompt_var")
        if prompt_var is None:  # catalog-sourced (prompt_file), asserted at converge time instead
            continue
        prompt = inline.get(prompt_var)
        if prompt is None:
            continue
        if not KEY.search(prompt):
            offenders.append(
                f"{job.get('name', job.get('job'))} fires "
                f"{_fires_per_day(job['schedule'])}x/day with no memory baseline"
            )
    assert not offenders, "\n".join(offenders)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
