# Laya fine-tuning

Fine-tuning is an opt-in, two-run workflow. The role must first be converged
normally so the Laya virtual environment, service unit, and pinned `laya-train`
command are installed. Run dispatch and collect against one host at a time:

```sh
ansible-playbook playbooks/llm-serving-gpu-engines.yml -i inventory/hosts.yml \
  --tags laya_systemone --limit <one host> \
  -e laya_systemone_finetune_enabled=true \
  -e laya_systemone_finetune_action=dispatch
```

Dispatch verifies the data and pinned base, stages the output directory, stops
Laya and a configured GPU peer when that peer was active, and starts the
`laya-finetune.service` transient unit with `systemd-run --no-block`. Training
output goes to the system journal. The Ansible run does not wait for training.
The persisted peer-state file is beside the staging directory. The Semaphore
schedule runs collect every 15 minutes through `ai-laya-finetune-collect`, so
the peer is restored within about 15 minutes of training ending.

Run collect separately. A scheduler may call this command repeatedly; collect
is idempotent and returns successfully while the job is still running.

```sh
ansible-playbook playbooks/llm-serving-gpu-engines.yml -i inventory/hosts.yml \
  --tags laya_systemone --limit <one host> \
  -e laya_systemone_finetune_enabled=true \
  -e laya_systemone_finetune_action=collect
```

Collect validates and promotes a successful checkpoint, restores Laya and only
the peer recorded as active by dispatch, runs bounded health checks, and
reports a sanitized failure class.

Dispatch caps each dataset transfer at 30 seconds and the first-time pinned
base fetch at 420 seconds. The pinned `model.safetensors` file is 843 MB; at
100 Mbit/s its wire-time lower bound is about 70 seconds, while 25 Mbit/s is
about 4.5 minutes, before protocol overhead. A fetch slower than the cap fails
the dispatch and can be retried later. Collect allows up to four minutes for
each health check, keeping both sequential waits under eight minutes.
