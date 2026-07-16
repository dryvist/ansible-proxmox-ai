# Curriculum job: apps — app-fleet health, logs cross-referenced with the repo

You are running as a one-off submitted job (not a cron). This is a graded
cross-source synthesis exercise: assess the health of the homelab application
fleet by combining what the logs show (Splunk) with what the owning repo
already knows (GitHub), and turn confirmed problems into actionable issues.

Context you can rely on: every infra LXC ships its host + service logs via
syslog to the `os` index; the apps themselves (Cribl, HAProxy, DNS, ntfy,
Mailpit, media stack, LLM fabric, your own gateway, etc.) are deployed by the
`dryvist/ansible-proxmox-apps` repo. Splunk-safety rails apply: bounded
queries only.

## Objective

Identify which deployed apps are actually unhealthy or noisy right now,
distinguish known problems from new ones, and leave the repo with
evidence-backed issues for the new ones.

## Scope

1. **Log survey**: over the last 24h in `index=os` (plus other indexes where
   relevant), rank hosts/services by error-and-warning volume (bounded stats
   by host, source, severity). Identify the top 5 noisiest or most
   error-prone services.
2. **Characterize each of the top 5**: what is the dominant message, is it
   new or long-running (compare the prior 24h window), is it harming function
   or just noise?
3. **Cross-reference**: search open issues in `dryvist/ansible-proxmox-apps`
   for each finding. Mark each finding KNOWN (cite the issue number) or NEW.
4. **Act on NEW findings only**: file at most 3 GitHub issues in
   `dryvist/ansible-proxmox-apps`, only for findings where your evidence is
   strong (concrete counts, a representative log line, time bounds). Title
   prefix `[hermes-fleet-health]`. Search for duplicates before filing. If
   the GitHub token is unavailable, include the fully-drafted issue bodies in
   the wiki page instead and say so.
5. **Fleet verdict**: one paragraph — overall fleet health, the single most
   concerning finding, and anything you could not assess from logs alone.

## Deliverable

- Wiki page `curriculum/apps-fleet-health`: the ranked table (service |
  24h error volume | trend vs prior 24h | dominant message | KNOWN/NEW |
  issue link or draft), per-finding characterizations, fleet verdict.
- Slack home channel post (≤15 lines): fleet verdict, the top finding, and
  links to any issues filed.

## Success criteria

- The top-5 ranking is reproducible from the SPL shown on the wiki page.
- Every KNOWN marking cites a real existing issue; every filed issue contains
  counts + a representative log line + time window.
- No duplicate issues filed; at most 3 filed total.

## Budget (hard)

At most 50 agent turns and about 75 minutes; at most 15 Splunk queries. On
budget exhaustion, publish what is complete, mark unassessed services
`NOT-ASSESSED (budget)`, and file no issues for anything under-evidenced.
