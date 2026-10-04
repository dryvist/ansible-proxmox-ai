# llamaindex

Deploys the RAG indexer: a Python venv running `index_docs.py` on a systemd
timer, embedding the configured sources (`llamaindex_sources`) through the
fabric router and building a single Qdrant collection.

The `llamaindex-index.service` journal records are forwarded by rsyslog over
the dedicated `llamaindex` Cribl Stream input to Splunk's `llm` index. The
listener port comes from the published `tofu_data.constants.ai_log_routing`
map; the role does not guess a port when that contract is absent.

## Installation

Ships with this repo; no external install. Wired into `playbooks/site.yml`
against the guest(s) tagged for the RAG indexer in the tofu inventory. Tools
come from the repo's Nix dev shell (`direnv allow`).

## Usage

```bash
ansible-playbook -i inventory/hosts.yml playbooks/site.yml --tags llamaindex
```

Run the indexer by hand on the guest for a dry run (fetches and reports
sizes; writes nothing):

```bash
/opt/llamaindex/venv/bin/python /opt/llamaindex/index_docs.py --dry-run
```

## Hardening

- A source with `required: true` failing to resolve fails the run
  (`index_docs.py` exits non-zero, and a converge-time check
  in `tasks/assert_hardening.yml` additionally verifies every required
  `path`-type source exists).
- `index_docs.py` touches `llamaindex_freshness_sentinel` (default
  `{{ llamaindex_data_dir }}/.last_successful_index`) after each successful
  index build; a converge-time probe fails if that file exists and is older
  than `llamaindex_freshness_max_age_hours` (default 48h), meaning the
  periodic refresh has stopped succeeding.
