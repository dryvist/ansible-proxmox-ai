# Cache tier benchmark runs

The `benchmark-run` playbook uses the upstream LMCache `multi-round-qa.py` tool
for W1, W2, and W3, and the upstream vLLM `benchmark_serving_multi_turn.py`
tool for the W2 cross-check. The runner downloads the pinned release sources,
records their SHA-256 values, runs them inside the pinned CPU vLLM image, and
archives the per-request outputs with the run metadata. The vLLM image and
multi-turn source version must match the serving version.

Cache-tier surveys select `lmcache-multi-round-qa` or `vllm-multi-turn` and
provide the contract configuration and workload fields. Cache-tier runs do not
upgrade the measured guest. The runner requires `/metrics` and cgroup memory to
be readable before it starts. It saves a `/metrics` snapshot before and after
the cell and every 15 seconds during it, along with
`/sys/fs/cgroup/memory.current`. The tool timer is limited to 18 minutes; the
runner allows up to 79 more seconds for warm-up and command setup. Before and
after captures are bounded, keeping the cell below its 20-minute cap.

Each successful LMCache cell emits one JSON line prefixed with `KVO_RESULT` and
a space in the Semaphore task log. It combines the suite CSV's per-round TTFT/TPOT/throughput summary,
vLLM cache configuration and cache metrics, cgroup `memory.max`/`memory.peak`,
and the active vLLM service main-process RSS. External cache counters are
included by their live metric names; rates remain `null` when the pinned engine
does not expose enough counters to calculate them.

The LMCache CSV contains one record per request. `question_id == 1` is the first
round; later `question_id` values are subsequent rounds. The vLLM cross-check
stores per-request statistics in its JSON output. Its generated conversations
use the configured common prefix, per-conversation history prefix, round count,
and answer length.

## Size the workloads

Use the engine's measured `gpu_kv_capacity_tokens` for `G`. Determine the
effective KV bytes per token (`B`) from the pinned model's actual full-attention
layers, KV heads, head size, and FP8 element size, and reconcile that with the
engine startup report. Hybrid attention makes a guessed layer count unsafe. If
`B` cannot be established from the pinned configuration and startup evidence,
stop before sizing or running cells.

Convert each configured CPU offload size to token capacity:

```text
Ccpu(O GiB) = floor(O * 2^30 / B)
```

Choose `A2` after measuring non-KV engine memory under the 64 GiB guest limit:

```text
A2_offload_GiB = floor(64 - measured_non_KV_peak_GiB)
```

The measured process footprint is the RAM reserved for the vLLM process. Apply
any additional reserve only when the lead has specified it. `A3` uses the same
CPU offload size as `A2`; use it only when the pinned vLLM/LMCache pair has been
shown compatible with the model. A3 dispatch requires `lmcache_compatibility_status=compatible`
and a non-empty `lmcache_compatibility_evidence`; an unverified pair is blocked
before the benchmark starts.

For the standard workload, set `S = 2048`, `H >= 32768`, `R >= 6`, and `A =
512`. The estimate for one user's full conversation is `U = H + R*A`; the
shared prefix is counted once while prefix caching is enabled. For a target
working set `T`, calculate:

```text
N(T) = max(1, round((T - S) / U))
W(N) = S + N*U
qps = N*R / duration_seconds
```

Use `duration_seconds = 180` for the approved three-minute dry run and no more
than `1080` for a matrix cell. The vLLM multi-turn tool receives per-client
rate `qps / N` and `2*R` messages per conversation.

Set the workload targets and check the resulting integer-sized working set
before dispatch:

| Workload | Target | Required capacity check |
| --- | --- | --- |
| W1 | `T = 0.60*G` | `0 < W(N) < G` |
| W2 | `T = 1.75*G` | `G < W(N) < G + Ccpu(24 GiB)` |
| W3 | `T = 1.50*(G + Ccpu(A2_offload_GiB))` | `W(N) > G + Ccpu(A2_offload_GiB)` |

If an integer `N` does not meet the required interval, do not dispatch that
workload. W1 is `fits_gpu`, W2 is `spills_to_ram`, and W3 is `exceeds_all` on
every arm. The class describes the estimated working set relative to the
configured tiers, independent of whether an arm enables offload. A0's W2 run
therefore has no external cache tier to hit. The post-run `/metrics` captures
must confirm the classification: GPU KV usage approaches saturation for W2/W3,
external cache hits appear only on offload arms, and W3 shows eviction from
both tiers. The sizing estimate alone does not prove the classification.

The exact per-cell request fields are:

```text
suite=lmcache-multi-round-qa
workload_id=W1|W2|W3
kv_arm=A0|A1|A2|A3
cache_workload=fits_gpu|spills_to_ram|exceeds_all
host_ram_limit_gib=64
prefix_caching=true
kv_offload_backend=none|native|lmcache
kv_offload_cpu_gib=<arm size>
kv_offload_disk_gib=0
weight_cpu_offload_gib=0
llamacpp_cache_ram_mib=0
gpu_memory_utilization=<served profile value>
max_num_batched_tokens=<served profile value>
gpu_kv_capacity_tokens=<engine startup value>
num_users=<N(T)>
num_rounds=<R>
shared_prompt_tokens=2048
user_history_tokens=<H, at least 32768>
answer_tokens=512
request_rate=<qps>
duration_seconds=<180 for dry run; at most 1080 per cell>
```

Dispatch each cell separately with the existing benchmark-run Semaphore
template. The KVO2 publisher converter remains responsible for mapping raw tool
outputs and telemetry to the result schema.
