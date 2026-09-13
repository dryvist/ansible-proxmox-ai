# llm_cpu_scaler

Demand-driven LXC start/stop for the CPU LLM pool. Installed on one
`llm-router-*` guest.

**Single source:** `tofu_data.constants.llm_cpu_scaler` (from deployment.json
`llm_cpu_scaler.name` — paths and tags are derived in tofu, never restated here).

| Fact | Source |
| --- | --- |
| Unit name / paths / timer | `constants.llm_cpu_scaler.*` |
| Pool / warm / moe tags | `constants.llm_cpu_scaler.tags` / `pool_tags` |
| Pool guests | containers matching those tags |
| Proxmox node | those guests' `node` |
| API host | `{nodes[node].role\|node}.{tofu_data.domain}` |
| Health FQDNs / VMIDs | each guest's published `ip` / `vmid` |

Secret only (not in tofu):

```sh
# path = constants.llm_cpu_scaler.env_file
PROXMOX_API_TOKEN_ID=...
PROXMOX_API_TOKEN_SECRET=...
```
