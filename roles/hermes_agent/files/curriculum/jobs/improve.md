# Curriculum job: improve — evidence-based self-improvement proposal

You are running as a one-off submitted job (not a cron). This is a graded
judgment exercise: review your own recent operation, find what is actually
limiting your usefulness, and propose (and where safe, apply) improvements.
The grade rewards evidence and honest prioritization, not volume of ideas.

## Objective

A prioritized, evidence-backed improvement plan for your own operation, with
the safe subset already applied.

## Scope

1. **Review your own recent history**: your cron jobs' recent outcomes
   (failures, [SILENT] rates, anything that never fires usefully), your
   memory (stale or contradictory baselines), and your wiki (gaps, orphaned
   or outdated pages). Cite specifics — job names, dates, page names.
2. **Find friction**: identify concrete recurring failure modes or wasted
   work (e.g. a sweep that always finds nothing, a query pattern that
   times out, a baseline that flaps). Each must cite at least one real
   observed instance from step 1.
3. **Propose top 5 improvements**, ranked by expected value. For each:
   the evidence, the change, and the implementation path — classify as
   SELF-SERVE (something you can change yourself: your own cron jobs,
   `splunk-auto-*` checks, wiki structure, memory hygiene) or ROLE-CHANGE
   (requires editing the Ansible role that manages you: config values,
   seeded cron prompts, new tools/secrets).
4. **Apply the safe subset**: apply at most 2 SELF-SERVE items now, and only
   reversible ones (add/adjust a self-created check, restructure wiki pages,
   correct memory). NEVER pause, delete, or edit the seeded cron jobs the
   role manages — propose changes to those as ROLE-CHANGE items instead.
   Record exactly what you changed.
5. **File ROLE-CHANGE items**: for up to 2 ROLE-CHANGE proposals, file a
   GitHub issue in `dryvist/ansible-proxmox-ai` (title prefix
   `[hermes-improve]`, evidence included, duplicate-checked). If the GitHub
   token is unavailable, put the full issue drafts on the wiki page and
   say so.

## Deliverable

- Wiki page `curriculum/improve`: the review findings with citations, the
  ranked top-5 table (evidence | change | path | applied?/filed?), a
  changelog of what you actually modified, and issue links/drafts.
- Slack home channel post (≤15 lines): the top 5 in one line each, what you
  applied, what needs a human.

## Success criteria

- Every proposal traces to cited evidence from your real history — no generic
  "I could be more proactive" items.
- Applied changes are reversible, listed in the changelog, and within the
  step-4 limits; seeded role-managed jobs untouched.
- Priorities are argued (why #1 beats #2), not just listed.

## Budget (hard)

At most 40 agent turns and about 60 minutes. On budget exhaustion, publish
the review + ranking without applying anything, marked `PROPOSAL-ONLY
(budget)`.
