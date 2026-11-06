# Model release watcher

A disabled-by-default controller timer proposes revision-pinned model trials.
It runs daily at 06:11 in the controller's timezone (`11 6 * * *`). It never
adds registry records or starts a serving engine. The native Ansible URI,
copy, and systemd modules provide discovery, the queue, and scheduling.

Set `llm_model_watch_enabled: true` and supply `llm_model_watch_accelerators`
in controller variables before running `playbooks/llm-model-watch.yml` with
privilege to manage systemd. Each accelerator requires `id`, `vram_gib`,
`reserve_gib`, `formats`, `libraries`, and `quantizations` (glob patterns).
Derive capacities from inventory. The weight budget is VRAM minus the runtime
reserve; it is a proposal estimate, and trial preflight still validates support.
Unknown quantization or file sizes, incomplete GGUF shards, private models,
and gated models produce no requests. Configure the controller's protected
environment file with `LLM_MODEL_WATCH_SLACK_WEBHOOK_URL` for the existing
JSON `text` webhook payload. Webhook tasks use `no_log`.

The timer persists its nonsecret inputs in `/etc/llm-model-watch.yml` and reads
`llm_model_watch_environment_file` at runtime. Keep the checkout and configured
Ansible executable installed at their configured paths. Reapplying with the
flag false stops and disables an existing timer.

The watched organizations default to Qwen, mlx-community, unsloth, nvidia,
and bartowski. Each run surveys the most recent
`llm_model_watch_limit_per_org` revisions per organization, default 100; this
is a bounded daily survey, not a historical backfill. Discovery uses the
[Hub API](https://huggingface.co/docs/hub/api), then revision-pinned model
information with file sizes and config metadata. New commits to a model are
new candidates.

Requests live under `llm_model_watch_queue_dir`. Their key includes repository,
commit SHA, and accelerator, and `copy` with `force: false` retains existing
requests across runs. Keep these files after trials as the proposal ledger;
removing a file permits that proposal to be queued again. The Slack summary
reports newly queued requests and the number of fitting candidates.

Consume a request through the existing trial playbook:

```sh
ansible-playbook playbooks/llm-model-trial.yml \
  -e llm_model_trial_request_file=/path/to/request.json
```

Consumption is explicit: the watcher does not invoke a trial. The current trial
playbook supports its existing single GPU target only. The request accelerator
ID must equal `llm_model_trial_host`; other accelerator proposals remain queued
for their own trial consumers. Revision and quantization must match the existing
trial selector before target-side tasks can run. Unsupported engine rules fail
preflight. Promotion remains a separate registry change.
