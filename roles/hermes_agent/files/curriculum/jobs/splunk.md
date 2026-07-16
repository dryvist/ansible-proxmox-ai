# Curriculum job: splunk — one deep investigation with an evidence table

You are running as a one-off submitted job (not a cron). This is a graded
depth exercise: ONE self-directed Splunk investigation, carried to a
defensible conclusion, with an evidence table. This is not a monitoring sweep
— do not run your usual triage pattern and stop; pick one thread and pull it.

All the query-safety rails of your splunk-monitor skill apply: bounded
queries only (tstats/stats/head, narrow explicit time windows), never a raw
unbounded search.

## Objective

Select the single most interesting unexplained signal currently visible in
Splunk, investigate it to a root-cause-or-characterized conclusion, and
present the reasoning as claim-by-claim evidence.

## Scope

1. **Select**: run 2-4 bounded survey queries across the indexes you know
   (unifi, os, firewall, network, honeypot if present) against your remembered
   baselines. Pick ONE candidate signal — the most significant deviation or
   the most consequential unexplained pattern. State in one paragraph what you
   picked, what you rejected, and why.
2. **Investigate**: iterate bounded queries to narrow the signal — time
   bounds, sources, hosts, ports/users involved, whether it is new or has
   history (compare a prior window of the same length), and correlation with
   any second index.
3. **Conclude**: classify the finding — benign-explained / needs-watching /
   anomalous-needs-human — with a confidence level and what evidence would
   change your mind.
4. **Persist**: update memory baselines with what you learned; if the finding
   deserves continuous watching, you may register ONE `splunk-auto-*` check
   (that is the only write outside wiki/memory permitted in this job).

## Deliverable

- Wiki page `splunk/curriculum-investigation-<topic-slug>`:
  - the selection paragraph,
  - an **evidence table**: one row per claim — claim | query run (the actual
    SPL) | key numbers returned | how it supports the claim,
  - the conclusion + confidence + what-would-change-my-mind,
  - queries attempted that returned nothing useful (dead ends count as work).
- Slack home channel post: the finding in ≤10 lines — what, where, since
  when, classification, recommended human action (or "none needed").

## Success criteria

- Every claim in the conclusion traces to a row in the evidence table.
- All SPL shown is bounded (explicit time window, tstats/stats/head shapes).
- The conclusion commits: a classification and confidence, not a hedge.
- Baselines/memory actually updated.

## Budget (hard)

At most 40 agent turns and about 60 minutes, and at most 15 Splunk queries
total. On budget exhaustion, publish the evidence table as-is with
classification `INCOMPLETE (budget)` and the single next query you would
have run.
