# Experiments

One script per claim. Each script should be runnable from the repository root,
print what it is measuring, and finish in a bounded amount of time.

Every script here must have a matching entry in
[`../docs/design-document.md`](../docs/design-document.md) stating what it
supports, how long it takes, and what output to expect.

| Script | Claim | Runs on | Time |
| --- | --- | --- | --- |
| `00_smoke_local.sh` | the pipeline works end to end (synthetic data, no Ceph) | any machine with Docker | ~2 min (+10 min image build once) |
| `01_check_cluster.sh` | the testbed is as described | compute node | ~10 s |
| `02_load_droid.sh` | fixed, real DROID 1.0.1 inputs are in CephFS | compute node | ~1 min per shard |
| `03_baseline_separate.sh` | separate-compute baseline: runtime, cross-host bytes | compute node | ~8 min per shard |
| `04_storage_node.sh` | same jobs on the storage node keep data off the network | compute node | ~8 min per shard |
| `05_verify_outputs.sh` | both modes' outputs are valid and identical | compute node | ~1-2 min |
| `06_background_service.sh` | loaded data is converted in the background | compute node | ~8 min per shard |
| `07_report.sh` | results table and chart for the slides | compute node | seconds |
| `run_all.sh` | 01 through 07 in order | compute node | ~30 min |

`common.sh` holds shared helpers. Settings come from `infra/cluster.env` (written by
the setup scripts); for example `SHARDS="2 8" experiments/run_all.sh` uses two shards.
