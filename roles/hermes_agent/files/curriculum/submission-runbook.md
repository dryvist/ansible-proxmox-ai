# Hermes curriculum — submission runbook (turnkey)

Runs only once the job API is live (the tofu ingress applied and this role
converged with `HERMES_API_SERVER_KEY` seeded in OpenBao `secret/ai/hermes`).
The API is key-presence-gated and the FQDN does not resolve before that
apply. `curriculum.yml` is the canonical manifest: job order, stagger,
budgets, and the machine-checkable `success_checks` graded in step 5.

All endpoints by FQDN only. Set once for the session:

```bash
HERMES_API="https://hermes-api.${PROXMOX_SUBDOMAIN:?set from Doppler/SOPS}"
CURR="$(dirname "$0")"   # this directory (the versioned curriculum)
CURL_TIMEOUTS=(--connect-timeout 15 --max-time 60)
```

## 0. Preflight gates (all must pass)

```bash
# Gate 1 — DNS + unauthenticated liveness (canonical post-converge probe)
curl "${CURL_TIMEOUTS[@]}" -sS -o /dev/null -w '%{http_code}\n' "$HERMES_API/health"
# expect: 200

# Gate 2 — negative auth test: keyless submit must be refused
curl "${CURL_TIMEOUTS[@]}" -sS -o /dev/null -w '%{http_code}\n' -X POST "$HERMES_API/v1/runs" \
  -H 'Content-Type: application/json' -d '{"input":"noop"}'
# expect: 401
```

If gate 1 is not 200: the converge has not landed — stop. Do not improvise a
path to the guest (no `pct exec`, no SSH); the API is the sanctioned path.

## 1. Fetch the bearer key (once, in-memory only)

The key is READ from OpenBao (`secret/ai/hermes`, field
`HERMES_API_SERVER_KEY`) — never seeded from this lane, never echoed, never
written to a file:

```bash
HERMES_API_SERVER_KEY="$(bao kv get -field=HERMES_API_SERVER_KEY secret/ai/hermes)"
[[ -n "$HERMES_API_SERVER_KEY" ]] || { echo "KEY MISSING — seed it first"; exit 1; }
```

## 2. Positive smoke test (one trivial run before the batch)

```bash
curl "${CURL_TIMEOUTS[@]}" -sS -X POST "$HERMES_API/v1/runs" \
  -H "Authorization: Bearer $HERMES_API_SERVER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"input":"Reply with exactly: curriculum-smoke-ok. Do not use any tools."}'
# expect: HTTP 202 + a run_id; then poll GET /v1/runs/<run_id> until completed.
```

Also confirm the seeded cron fleet's pause state via `/api/jobs` — a paused
fleet does not block one-off runs but belongs in the report.

## 3. Submit the jobs, staggered

Order and stagger come from `curriculum.yml` (easy → hard, ~20 min apart —
the stagger bounds concurrent load on the shared brain while the seeded
Splunk crons are also firing):

```bash
SUBLOG="$CURR/submissions.log"
while IFS=$'\t' read -r job stagger_minutes; do
  ts=$(date -u +%FT%TZ)
  resp=$(jq -n --rawfile p "$CURR/jobs/$job.md" '{input:$p}' \
    | curl "${CURL_TIMEOUTS[@]}" -sS -X POST "$HERMES_API/v1/runs" \
        -H "Authorization: Bearer $HERMES_API_SERVER_KEY" \
        -H 'Content-Type: application/json' -d @-)
  echo "$ts $job $resp" | tee -a "$SUBLOG"
  if (( stagger_minutes > 0 )); then
    sleep "$((stagger_minutes * 60))"
  fi
done < <(yq -r '.jobs[] | [.id, .stagger_minutes] | @tsv' "$CURR/curriculum.yml")
```

Each iteration logs a 202-style body with a `run_id`. If a submit fails, log
it and continue (no retry loops); resubmit that one job manually later.

## 4. Collect results

Runs are long (budgets 45–75 min each). Per job:

```bash
curl "${CURL_TIMEOUTS[@]}" -sS -H "Authorization: Bearer $HERMES_API_SERVER_KEY" \
  "$HERMES_API/v1/runs/<run_id>"          # status + final output
# or stream live: .../v1/runs/<run_id>/events
```

- **Slack home channel** — each job ends with a summary post (delivery proof).
- **Hermes wiki** — the substantive artifacts (pages named in each job's
  `success_checks`).
- **GitHub** — `apps`/`improve` may file capped issue sets in
  `dryvist/ansible-proxmox-apps` (prefixes `[hermes-fleet-health]`,
  `[hermes-improve]`).

Save each run's final status JSON to `$CURR/results/<job>.json` for the
grader.

## 5. Grade

First evaluate every `success_checks` gate from `curriculum.yml` (run object,
event stream, GitHub — never the job's own summary). Then score per
`grading-sheet.md` (4 dimensions × 0–3 per job + spot-checked verified-claim
rate + the cross-job omissions check), and fill the retrospective feature
values in `escalation-rubric-schema.md` from the transcripts. These runs are
the **broad-tier** datapoints; matched deep-tier packets rerun selected jobs
when a cluster window is up.
