# Hermes repo split — recommendation

Where Hermes content should live: Ansible mechanics here, prompt bodies in the
`dryvist/ai-llm-prompts` catalog.

Measured against `roles/hermes_agent` on `develop` (2026-07-25).

## The finding that changes the question

**The split already exists, is wired, and works.** `defaults/main.yml` pins the
catalog at an exact commit:

```yaml
hermes_agent_prompts_flake_ref: >-
  github:dryvist/ai-llm-prompts/<commit>#auto-ai-agent
```

and `tasks/main.yml` resolves prompt bodies out of it by filename — the eight
`hermes-digest-*.md` files, the inbound-webhook prompt, and five curriculum
prompts. The role's own comment states the contract precisely:

> A prompt edit lands in the catalog first; this pin then moves to that merge
> commit, so the converge is the only thing that changes what a job says.

So this is not a proposal to start a split. It is a proposal to **finish the one
already proven**, using the mechanism already in production. That materially
lowers the risk: no new pattern, no new consumer code, no new failure mode — the
remaining work is moving content onto a path that already carries content.

## What is left behind, measured

Everything below is prompt text that a model reads, currently living in this
repo rather than the catalog.

| Content | Where | Size |
| --- | --- | --- |
| 8 cron/card prompt bodies | `defaults/main.yml` block scalars | **218 lines** |
| Curriculum (rubric, grading sheet, runbook, index) | `files/curriculum/` | **21,261 B** across 4 files |
| Per-profile SOUL personas | `templates/soul-*.md.j2` | **2,402 B** across 2 files |

The prompt bodies are the strongest case. `defaults/main.yml` is **1,788 lines,
913 of them comments** — a defaults file that is more than half prose, carrying
another 218 lines of model-facing text. Both problems shrink at once by moving
the prompts out.

## The boundary rule

One test, applicable to anything added later:

> **Does a model read it, or does a machine execute it?**
>
> Model reads it → catalog. Machine executes it → this repo.

Applied:

- **Catalog**: prompt bodies, SOUL personas, curriculum text, rubrics, grading
  criteria — anything whose only consumer is an LLM's context window.
- **This repo**: tasks, handlers, systemd units, shell and Python templates,
  ports, URLs, feature toggles, cron schedules, concurrency caps — anything a
  machine parses or runs.

The rule resolves the awkward cases without argument. `curriculum.yml` is an
*index* of prompt files, so it moves with them. A digest's **schedule** stays
(cron parses it) while the digest's **prompt** moves (only the model reads it).

## Recommended order

Lowest risk first; each step is independently revertible and independently
converge-testable.

1. **The 8 prompt bodies.** Same shape as the eight already externalized, so it
   is a mechanical extension of a working path: add the `.md` files to the
   catalog, replace each block scalar with a `lookup` against
   `hermes_agent_prompts_path`, bump the pin. Highest value, lowest novelty.
2. **Curriculum.** Already referenced by filename through
   `hermes_agent_curriculum_prompt_files`, so the consumer indirection exists;
   only the file location changes.
3. **SOUL templates.** Move last. They are Jinja templates, not static files, so
   they need either a rendering step in the catalog or their variables resolved
   at the boundary. Least urgent (2.4 KB) and most likely to need design.

## What it buys, and what it costs

**Buys.** A prompt change stops being an infrastructure change: editing what a
job *says* no longer touches the repo that decides how the guest is *built*, so
it no longer inherits Ansible review, ansible-lint, or a converge to be seen.
Prompts become reviewable as prose by anyone, versioned in one catalog across
every agent that uses them, and diffable without Ansible noise. It also cuts
this repo's largest file roughly in half.

**Costs, stated honestly.** Two repos must move together for a prompt change to
reach a guest: catalog merge, then pin bump here. That is one extra PR per
prompt change. The existing arrangement already pays this cost for eight
prompts, and the pin is what makes a converge reproducible — a floating
reference would be worse, not better. The cost is real but already accepted and
already amortized.

**Not recommended:** moving `defaults/main.yml`'s configuration, the task files,
or any template that renders a unit, script, or Python program. Those are the
mechanics, they are what Ansible exists to own, and splitting them would put a
guest's build across two repos for no gain.

## Status

Recommendation only. No files moved; nothing here changes behaviour.
