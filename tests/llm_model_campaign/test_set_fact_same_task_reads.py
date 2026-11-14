from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
TASK_FILE_GLOBS = [
    "roles/llm_model_trial/tasks/*.yml",
    "playbooks/tasks/llm-model-campaign*.yml",
]

# Deliberate reads of an accumulator the same task also updates. Each entry is
# (file, task name, fact read); a stale entry fails the test.
ALLOWED_SAME_TASK_READS = {
    # "pending" excludes the repos "infos" recorded before this task and drops
    # the id recorded by this task explicitly.
    ("roles/llm_model_trial/tasks/upstream.yml",
     "Record the upstream metadata by requested repository ID",
     "llm_model_trial_upstream_infos"),
    # "infos" consumes the head of the "pending" queue set by the preflight
    # initializer and each earlier recursion.
    ("roles/llm_model_trial/tasks/upstream.yml",
     "Record the upstream metadata by requested repository ID",
     "llm_model_trial_upstream_pending"),
}


def _task_files() -> list[Path]:
    files: list[Path] = []
    for pattern in TASK_FILE_GLOBS:
        files.extend(ROOT.glob(pattern))
    return sorted(files)


def _walk(tasks: list) -> list[dict]:
    walked: list[dict] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        walked.append(task)
        for section in ("block", "rescue", "always"):
            nested = task.get(section)
            if isinstance(nested, list):
                walked.extend(_walk(nested))
    return walked


def _reads_fact(value: str, fact: str) -> bool:
    # Match the bare fact only: a longer identifier (llm_model_trial_hub_tree_raw)
    # or an attribute of another object (hostvars['localhost'].llm_model_trial_id)
    # resolves to something set earlier, not this task's fact.
    return re.search(rf"(?<![A-Za-z0-9_.]){re.escape(fact)}(?![A-Za-z0-9_])", value) is not None


def test_trial_and_campaign_tasks_do_not_read_facts_set_by_the_same_task() -> None:
    reads: dict[tuple[str, str, str], list[str]] = {}
    for path in _task_files():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, list):
            continue
        relative = path.relative_to(ROOT).as_posix()
        for task in _walk(document):
            facts = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if not isinstance(facts, dict):
                continue
            for fact in facts:
                readers = [other for other in facts
                           if other != fact and _reads_fact(str(facts[other]), fact)]
                if readers:
                    reads[(relative, task.get("name", ""), fact)] = readers

    unexpected = {entry: readers for entry, readers in reads.items()
                  if entry not in ALLOWED_SAME_TASK_READS}
    stale = ALLOWED_SAME_TASK_READS - set(reads)
    details = "\n".join(f"{entry[0]} task {entry[1]!r}: {readers} read the same-task fact {entry[2]}"
                        for entry, readers in unexpected.items())
    assert not unexpected, "set_fact values read facts their own task sets:\n" + details
    assert not stale, f"allow-list entries no longer match any same-task read: {sorted(stale)}"
