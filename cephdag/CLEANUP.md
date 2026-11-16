# Cleanup of the refinement-v2 lab

The first commit of this repository is the refinement-v2 source exactly as frozen in
`~/.hermes/experiments/computational-storage-refinement-20261103/project/` (without
`deps/wheels`, `state/`, `results/` and build output). Everything below is the
difference from that commit. Removed code is still in that commit and in the frozen copy.

## Where every original file went

### Package `cephdag/`

| Original | Now | Notes |
| --- | --- | --- |
| `graph.py` | `graph.py`, `functions.py` | `Manifest` became `Dataset` (no `shard_limit`); `BUILTINS` moved into the registry; pools and type tags are no longer restricted to `node[0-2]` and `float32` |
| `model.py` | `model.py` | `Simulation` takes the pool list; node count comes from the models; `refresh_observed` is `refresh`; `candidates()` moved here from `schedulers.py` |
| `schedulers.py` | `policies/baselines.py`, `policies/greedy.py`, `policies/__init__.py` | `RandomScheduling`, `SingleNode`, `DataLocal`, `EarliestFinish` (now `Greedy`), `WaitAwareGreedy` (now `GreedyWait`), `make_scheduler` (now `make_policy`). Removed: `LoadBalancing`, Python `MCTS` and `SearchNode` |
| `list_schedulers.py` | `policies/list.py` | HEFT and `WorkStealing` kept. PEFT and CPOP removed: they need their own rank tables, critical path and downstream estimates, so they are not a flag on HEFT. `allow_steal=False` option removed |
| `native_planner.py` | `policies/native.py` | `NativeGraph` and `NativeMCTS` (now `NativeMcts`, no longer a subclass of the Python MCTS); raises a clear error on clusters that are not three nodes |
| `search_alternatives.py` | `policies/native.py` | `NativeBeam` (now `Beam`) kept; `NativeSchedule` folded into `NativeGraph`; `NativeLocalSearch` (local/anneal) removed |
| `cpsat.py` | removed | OR-Tools policy; the dependency is gone too |
| `refinement.py` | removed | contention controller, matched lookahead, exhaustive search, tracing |
| `runtime.py` | `coordinator.py` | same loop, split into a `Run` class; worker URLs come from the cluster file; transport and store are passed in |
| `worker.py` | `worker.py` | `DurableWorker` protocol unchanged; the unused non-durable `Worker` base class removed; binds to the address in the cluster file; `--functions` loads extra modules |
| `storage.py` | `storage.py` | pools come from the cluster file; `MemoryStore` added for tests and examples |
| `workloads.py` | `functions.py` | numeric kernel and `synthetic` unchanged; `register` replaced by the `@function` decorator |
| `sdk.py` | `client.py` | `Client` now owns cluster, models, store and transport; adds `put_dataset`, `dataset`, `read`; `Job.run_id` |
| `__init__.py` | `__init__.py` | exports the client API |
| (new) | `cluster.py`, `lab-cluster.json` | node list replaces the hard-coded `172.30.{80+n}.10` addresses |

### `experiments/`

| Original | Now |
| --- | --- |
| `run.py` | `run.py`: one workload × policies × repeats. Kept the checksum readback, cross-policy output agreement, scalar oracle, own-payload cleanup with tombstone, Docker free-space guard and topology gate. Removed resume/checkpointing, the fixed policy matrix and the idle-traffic baseline |
| `oracle.py` | `oracle.py`, same arithmetic |
| `calibrate.py` | `calibrate.py`, same samples and fit; now acknowledges each sample job |
| `alternatives.py` | `build_workload("competing")` in `run.py`; rest removed |
| `refinement_run.py` | `build_workload("fanout" / "divergent")` in `run.py`; rest removed |
| `report.py`, `alternatives_report.py`, `native_report.py` | `summarize.py` (medians and ranges per case and policy) |
| `audit_results.py`, `benchmark_native.py`, `cached_planner.py`, `finish_budgets.py`, `finish_matrix.py`, `native_audit.py`, `native_memory.py`, `native_system.py`, `profile_planner.py`, `refinement_diagnostics.py`, `retention_probe.py`, `smoke.py`, `stealing_sensitivity.py` | removed: campaign-specific |

### Everything else

| Original | Now |
| --- | --- |
| `README.md` | rewritten for a new reader |
| `examples/client.py` | `examples/quickstart.py`, plus `examples/custom_function.py` |
| `native/src/*.rs` | unchanged apart from `cargo fmt` (only `alternatives.rs` changed); same ABI |
| `native/README.md` | rewritten; the old one described removed campaign scripts |
| `infra/lab.py`, `network.py`, `probe_boundaries.py` | generalized to N nodes from `infra/labs/<name>.json`, with an optional second copy of each pool (`infra/README.md`); the three-node config issues the original commands |
| `infra/compose.json` | removed: `lab.py` generates the Compose project from the lab config |
| `infra/Dockerfile` | also installs an optional DROID wheel lock |
| `infra/entrypoint.sh` | unchanged |
| `infra/recover_initial_setup.py` | removed (one-off recovery) |
| `infra/requirements-linux-*.lock`, `wheels-lock.json` | reduced to `numpy` and `pip`; OR-Tools and its dependencies removed |
| `tests/test_graph.py`, `test_model.py`, `test_worker.py`, `test_retention.py`, `test_runtime.py`, `test_schedulers.py`, `test_list_schedulers.py`, `test_native_planner.py`, `test_search_alternatives.py`, `test_oracle.py`, `test_experiments.py` | rewritten as `test_graph.py`, `test_model.py`, `test_worker.py` (with the retention tests), `test_coordinator.py`, `test_policies.py`, `test_native.py`, `test_experiments.py` |
| `tests/test_infra.py` | rewritten for N-node labs; with `tests/fake_host.py` and `tests/fixtures/lab-3node.json` it runs the lifecycle against a recording fake of Docker |
| `tests/test_cpsat.py`, `test_refinement.py`, `test_refinement_campaign.py`, `test_alternatives_campaign.py`, `test_native_campaign.py`, `test_cached_planner.py`, `test_report.py` | removed with their code |
| (new) | `test_equivalence.py`, `test_wire.py`, `test_client.py`, `tests/fixtures/` |

## Size

Lines as counted by `wc -l`; the original packs several statements per line, so the
count of non-whitespace characters is the fairer measure of how much code there is.

| Part | Before: lines | Before: characters | After: lines | After: characters |
| --- | --- | --- | --- | --- |
| Core package (without policies) | 1,184 | 40,870 | 2,019 | 52,153 |
| Policies | 1,091 | 44,777 | 746 | 19,640 |
| Core and policies together | 2,275 | 85,647 | 2,765 | 71,793 |
| Tests | 1,525 (19 files) | 65,680 | 1,716 (11 files) | 52,938 |
| Experiments | 1,922 (22 files) | 94,714 | 646 (5 files) | 19,590 |
| Rust (`native/src`) | 1,221 | 27,842 | 1,478 | 27,852 |

The core package grew because of the new pieces (cluster file, function registry,
client with datasets) and because every statement now has its own line. The equivalence
fixture generator (589 lines) and the fixtures are not counted under tests.

## Behaviour changes

For the kept policies on the three-node lab there are none that the tests can see:
every recorded decision, forecast, whole simulated run and live-state reconstruction of
the original matches (`tests/test_equivalence.py`), and the coordinator sends the
original requests (`tests/test_wire.py`). Deliberate changes:

- Any number of nodes, pools named freely, worker addresses from a JSON file.
- Type tags are free-form labels; an output estimate rounds to whole items of its type
  (4 bytes for `float32`, 1 byte otherwise).
- Declared output sizes are estimates. The coordinator sizes each dispatched task from
  its actual input sizes; for the builtins this is the same payload as before.
- The new worker checks the memory bound again with the actual output sizes, and no
  longer fails a function whose output differs from the declared size.
- Tasks may carry extra `params`; `ratios` and `iterations` are reserved.
- The default policy of `Client.submit` is `greedy` (the old `earliest-finish`).
- Policy names `load-balancing`, `mcts-*`, `cpsat`, `peft`, `cpop`, `local-*` and
  `anneal-*` no longer exist.
- `calibrate.py` acknowledges its sample jobs, so they leave worker memory.

## Open issues

- Not yet run on Ceph. The in-memory store and in-process workers stand in for RADOS and
  HTTP in every test.
- The worker on the lab is still the v2 worker. Custom functions, other type tags,
  estimated sizes and other node counts need the new `cephdag.worker` deployed.
- Existing lab datasets were written by the old code with keys `shard-NNNNNN` and a
  `shard_limit`; `Client.dataset` opens them, new datasets use `part-NNNNNN`.
- The trimmed image locks have not been used to build an image, and `deps/wheels` is
  not in this repository.
