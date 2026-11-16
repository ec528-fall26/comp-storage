# How the Demo 3 system and lab are built

This file covers the setup behind the numbers in DEMO-DATA.md: the question, the lab, the
software, the scheduler interface, the workloads, the method, what was reused, and the
limits. Every statement cites a source. Code paths are in the cephdag repository at commit
`8cdc2b4` (branch `mcts-lab`), the code deployed for the latest runs (PLAN.md:171, 227-228).
Doc paths without a directory are in `~/projects/comp-storage/demo3/`. Diagrams:
`arch_lab.png` (the lab) and `arch_software.png` (the software and one task's path).

## 1. The question and the comparison

The research question: when a task graph runs on machines that both store and compute, and
the placement of each task and of each output are separate decisions (waiting is also
allowed), does planning ahead beat cheap locality and greedy rules once planning time is
counted? The objective is wall time with planning included; network bytes break ties
(README.md:3-8; `cephdag/model.py:168-178`).

The demo compares two architectures on one Ceph cluster holding the same data:

- **Computational storage (cs):** a worker runs on each storage node, next to that node's
  OSD and pool, so a task can read its inputs and write its outputs locally.
- **Disaggregated baseline (disagg):** the storage nodes keep their OSDs and pools, but no
  workers run on them. Workers run on separate compute-only nodes, so every input and
  every output crosses the network (infra/README.md:12-24; README.md:24-28).

**Why the baseline is fair.**

- Same bytes: one Ceph cluster serves both modes, and the data is not copied
  (PLAN.md:17-23).
- Same resources: a storage node's quota is an OSD share plus a worker share, and a
  compute-only node gets exactly that worker share. All links are shaped to the same rate
  (infra/README.md:26-32).
- Baseline placement: the scheduler, fifo-hash, places each output by a stable hash of
  its name, the way CRUSH places objects (`cephdag/policies/baselines.py:68-82`;
  `cephdag/model.py:186-193`).
- Placement for other policies: on a role-split cluster every policy's outputs go where
  the hash puts them, and a policy chooses only the task and the worker
  (`cephdag/model.py:276-281`).
- Equal worker slots: both sides can run two workers per container (cs2, disagg2), so the
  headline comparisons put 16 worker processes on each side (PLAN.md:123-125;
  DEMO-DATA.md:27).
- Same output check: every run is checked for identical output digests across policies and
  modes (DEMO-DATA.md:22-23).

**Where it is not fair (FAIRNESS.md).**

- Single slot: each worker runs one task at a time, strictly read → compute → write →
  publish, with no prefetch in either mode. A disagg worker's receive link therefore idles
  while it writes (FAIRNESS.md:22-26, 55-58). A real disaggregated engine would overlap
  these; the overlap estimates are in FAIRNESS.md:63-68. The disagg2 mode partly stands in
  for overlap: two workers per compute container, so one worker's transfers overlap the
  other's compute (RESULTS-overlap.md:5).
- Persisted intermediates: every stage's output is written to storage, which favours cs most
  on DROID. A fused pipeline cuts the slot-matched advantage from 4.30x to 1.79x
  (FAIRNESS.md:82-92; DEMO-DATA.md:90-93).
- Coordinator placement: the coordinator runs in n0, and compute-only workers keep their job
  records in pool node0. This costs disagg about 2-6% (FAIRNESS.md:42-53, 93).

## 2. The lab: cephdag8 on aakashpc

**Host.** aakashpc has 32 logical CPUs and 123 GiB of RAM (infra/README.md:201). Its CPU is
a 9950X3D (LAB-VALIDATION.md:255), and it runs kernel 7.3.3-1-cachyos
(LAB-VALIDATION.md:40). It has two disks:

- An NVMe SSD for the root. Docker data and the OSD volumes live here, on btrfs
  (LAB-VALIDATION.md:213; PLAN.md:198-200).
- A 14.6 TB ext4 HDD, `/mnt/mass`. It holds the raw DROID shards, results, logs and archives
  under `/mnt/mass/cephdag` (PLAN.md:11-15).

The OSDs stay on the SSD on purpose. On the one shared HDD, each of eight OSDs would get
about 30 MB/s, which is less than a 400 Mbit link carries. Remote reads would then beat
local ones, and the comparison would be inverted (infra/README.md:192-194; PLAN.md:11-15).

**17 containers.** The whole lab is described by one config file,
`infra/labs/cephdag8.json` (image `cephdag-lab:20.2.4-amd64-scale20261112`).

| containers | role | runs | Docker limits (LAB-VALIDATION.md:97) |
|---|---|---|---|
| `cephdag8-n0..n7` | storage nodes 0-7 | one OSD, pool `node<I>`, a cephdag worker; n0 also runs the Ceph monitor, the manager and the coordinator | 3 CPU (OSD 1 + worker 2), 6144 MiB (3072 + 3072) |
| `cephdag8-c0..c7` | compute-only nodes 8-15 | a cephdag worker, the Ceph client config and keyring; no OSD, no pool | 2 CPU, 3072 MiB |
| `cephdag8-router` | router | forwards and shapes every link | 0.25 CPU, 128 MiB |

The shares come from `cephdag8.json:6-9`, and the roles from infra/README.md:3-10. Every
container is capped at 512 pids and gets only `CAP_NET_ADMIN`, with no privileges
(LAB-VALIDATION.md:97). The CPU budget is 24.25 of 32 CPUs in each mode. The quotas add up
to 40.25 in disagg, because the storage nodes keep their idle worker share there
(infra/README.md:184-186, 205).

**Ceph.**

- Version: Ceph 20.2.4 (LAB-VALIDATION.md:8).
- OSDs: eight, one per storage node, each a sparse 56 GiB BlueStore block file on a Docker
  volume, 448 GiB in total (`cephdag8.json:10`; LAB-VALIDATION.md:214).
- Pools: pool `node<I>` has 32 placement groups and is pinned to OSD I by CRUSH rule
  `pin<I>` (`infra/lab.py:25, 353-365`). `verify` confirmed that all 32 PGs and the test
  objects map to OSD I only (LAB-VALIDATION.md:97).
- Replicas: one (`replicas: 1`, size 1 / min_size 1). Two copies would halve the dataset and
  add a network write per output that the cost model does not charge
  (PLAN.md:25-28; infra/README.md:160-178).
- Memory: `osd_memory_target` is 80% of the OSD share, 2.4 GiB (infra/README.md:43;
  LAB-VALIDATION.md:205).
- Scrubbing: off (`noscrub,nodeep-scrub`), so that scrubs do not disturb timings
  (LAB-VALIDATION.md:121).

**Network.**

- Addressing: node K has its own /24 subnet, 172.30.144-159, with the node at .10 and the
  router at .254 (infra/README.md:46, 54).
- Routing: nodes reach each other through IPIP tunnels to the router
  (LAB-VALIDATION.md:17, 98).
- Shaping: every node's eth0 and every router port runs
  `tbf rate 400Mbit burst 128Kb lat 400ms` (LAB-VALIDATION.md:17).
- Verification: `lab.py verify` makes each node send two iperf3 flows at once to its ring
  successors, then receive two, and gates the sums at 70-120% of the rate
  (infra/README.md:117-122).
- Measured rates: 376.8-380.2 Mbit/s in both modes; a NIC-counter probe gave 377.8-377.9.
  This is 378 Mbit/s of TCP goodput (94.4%). Calibration uses 47.09 MB/s per NIC
  (LAB-VALIDATION.md:105-113, 127).
- Router headroom: 8 simultaneous flows got 3005 Mbit/s in aggregate
  (LAB-VALIDATION.md:115).
- Why tbf rather than policing: an earlier attempt that policed with nftables (dropping
  rather than queueing packets) was discarded. It changed disagg timings by up to 2x
  (LAB-VALIDATION.md:25-37).

**Modes and switching.** `infra/lab.py` has four modes (`infra/lab.py:28, 73-78`):

| mode | workers | cluster file |
|---|---|---|
| cs | one per storage node (8) | `cephdag/clusters/cephdag8-cs.json` |
| disagg | one per compute-only node (8) | `cephdag8-disagg.json` |
| cs2 | two per storage node, ports 8080 and 8081 (16) | `cephdag8-cs2.json` |
| disagg2 | two per compute-only node (16) | `cephdag8-disagg2.json` |

In the cs2 and disagg2 cluster files, each container's second worker appears as an extra
node without a pool that shares the first worker's host, CPUs, memory and link
(`infra/lab.py:74-77`; `cephdag/clusters/cephdag8-cs2.json`).

A switch to a cs mode stops the compute-only containers and starts the storage nodes'
workers. A switch to a disagg mode stops the storage nodes' workers, starts the compute
containers, reshapes their links to the recorded rate and starts their workers. Either
switch waits until every worker of the mode answers `POST /state`, and repeating a switch
changes nothing (infra/README.md:87-99). Measured: `mode cs` 12-13 s, `mode disagg` 4 s
(LAB-VALIDATION.md:99).

**Where results go.** `results_dir` `/mnt/mass/cephdag` (HDD) is mounted at `/results` in
every node container (`cephdag8.json:15`; infra/README.md:49).

## 3. Software architecture

**Client API** (README.md:81-129; `cephdag/client.py`):

- `Cluster.load(path)` reads a cluster file. The file lists each node's pool and worker
  URL (or null), the metadata pool and the RADOS namespace (`cephdag/cluster.py:105`).
- `Client(cluster, models=...)` opens the store.
  - `put_dataset(name, parts, placement=[...])` writes one object per part, round-robin by
    default.
  - `dataset(name)` opens an existing dataset; `delete_dataset` removes one.
  - `read(ref)` reads an object (`client.py:59-143`).
- `Graph()` builds the task graph: `map(function, dataset, iterations=, ratios=)`, `reduce`,
  and the lower-level `add` (`cephdag/graph.py:157, 202, 213`). `ratios` gives each output's
  size as a multiple of the input size; `iterations` is a cost hint (README.md:117-122).
- `client.submit(graph, scheduler, **options)` takes a policy name or any object with
  `choose()`. It returns a `Job` that runs in a background thread; the job has `wait`,
  `status`, `outputs` and `metrics` (`client.py:14-55, 121`).
- Functions are registered once, with a decorator: `@function(name, version, inputs=,
  outputs=)` (`cephdag/functions.py:38`). The client, the coordinator and every worker import
  the same modules, and code never travels over the network (`functions.py:1-5`).

**ObjectRef.** A stored object is named by `pool, key, size, dtype, partition, sha256`
(`cephdag/graph.py:15-23`). A not-yet-produced output is an
`OutputRef(graph, task, output, dtype, partition)` (`graph.py:46-56`). Outputs are written
under `runs/<run_id>/<task>/output-<i>` (`cephdag/worker.py:116`).

**Coordinator.** It runs in node 0 (README.md:46) and loops until every task has completed
(`cephdag/coordinator.py:330-352`):

1. Poll every worker's `/state` in parallel (`_poll`, `coordinator.py:318-328`).
2. Absorb completions. Record the output refs, acknowledge each completed job with its token
   and update the live picture (`_absorb`, 368-393).
3. Rebuild a `model.Simulation` of the live cluster: done tasks, where their outputs are,
   and how far each running stage has got (`observe`, 354-366).
4. Call `policy.choose(state)` and time it (`_decide`, 395-416).
5. If the policy returns `None`, wait. The coordinator re-polls every 5 ms until a stage or
   status changes (`_wait_for_change`, 418-442).
6. Otherwise POST `/submit` to the chosen worker with `run_id`, `worker`, `targets`, the
   resolved input `ObjectRef`s and the task (`_dispatch`, 444-472).

After the timed loop, the coordinator reads back every final output against its checksum
and saves `graph.json` and `result.json` to the metadata pool (`coordinator.py:273-283`).

**Worker HTTP endpoints** (`cephdag/worker.py:392-398`):

| endpoint | what it does |
|---|---|
| `POST /submit` | Starts a task if the single slot is free. It first writes a durable "running" record, which fences retries; a retry with the same payload returns the same record (`worker.py:220-262`, docstring 160-166). |
| `POST /state` | Reports the requested jobs and the interface byte counters; the coordinator's poll. |
| `POST /warm` | Reads local objects once, so page caches start alike in every run (`worker.py:216-218`; `experiments/run.py:174-186`). |
| `POST /ack` | Releases a terminal job from RAM. The ack token must match, and the durable copy must read back identical (`worker.py:352-367`). |
| `POST /recover-publication` | Republishes the exact terminal record after a failed publication, without re-running the task (`worker.py:316-351`). |

**One task's path** (`execute_payload`, `worker.py:37-145`):

1. Read each input from RADOS and check its size and SHA-256.
2. Run the registered function.
3. Write each output to the pool of the node the policy chose (`pools[target]`).
4. Publish a single `complete.json`, the only proof that the task finished.

A worker refuses a task whose inputs and outputs exceed its 512 MiB reservation
(`graph.py:10`; `worker.py:68-70, 109-111`). Job records go to the worker's host pool, or to
the metadata pool node0 for a compute-only worker (`cephdag/cluster.py:85-88`). At most 64
unacknowledged jobs are kept in RAM (`worker.py:168`). The acknowledgment protocol fixed the
2026-11-03 out-of-memory failure (README.md:38-42).

**Output verification.** `experiments/run.py` does four checks (`run.py:7-9`;
README.md:186-195):

- it reads every final output back against its checksum;
- it requires all policies to produce byte-identical outputs;
- it recomputes sampled values with an independent scalar oracle (`experiments/oracle.py`);
- it writes one digest of all output checksums per run.

The campaign driver then requires one digest across all modes of a case
(`experiments/campaign.py:31-34`). DROID waves are verified differently: videos are decoded
back, statistics recomputed, Parquet read back and sampled frames re-derived
(README.md:166-169).

## 4. The scheduler interface

**The interface.** A policy has one method, `choose(state)`. The coordinator calls it at
every event with a `model.Simulation` of the live cluster (`cephdag/policies/__init__.py:1-6`).
It returns either:

- `Dispatch(task, worker, targets)`: run this task on that worker and store output i on node
  `targets[i]` (`cephdag/model.py:196-202`); or
- `None`: wait for the next event.

Policies are built by name in `make_policy` (`policies/__init__.py:38-52`).

**The cost model and the forecast.** `Models` holds three parts (`model.py:15-166`):

- per-node read and write rates;
- per-node send and receive rates;
- a compute model: fixed seconds plus terms per input byte, per byte per iteration and per
  output byte, with separate fixed + per-input-byte + per-output-byte fits for each DROID
  function.

`experiments/calibrate.py` fits these models on dedicated samples, never on comparison runs.
Every worker runs samples at the campaign's shard size at the same moment, because workers
on one host share caches and memory bandwidth. The network rate is the verified shaped rate
(`calibrate.py:1-23`). The first cs calibration gave, for example:

- compute: 0.50 ms fixed, 1.73e-10 s per input byte, 5.36e-11 s per byte per iteration;
- DROID: 50.4 CPU-s per source GB (LAB-VALIDATION.md:129-135).

The scheduler campaigns used model file `30f993fb…`, whose read rates range 264-392 MB/s
(SCHEDULERS.md:5, 57).

The forecast, `model.Simulation`, plays reads, compute and writes forward. Concurrent stages
get an **equal share** of each disk and link they use (`model.py:264-268`). The demo also
used a **max-min fair-sharing** variant, `FairSimulation` in `demo3/sched_forecast.py`, for
offline forecasts (SCHEDULERS.md:166-174). Equal sharing misjudges moves off a hot node: a
47 MB/s transfer takes half of that node's disk (SCHEDULERS.md:170-172). With fair sharing
the forecast was within -8% to +14% of 24 measured cells (SCHEDULERS.md:193). The fair
forecast in Rust exists only on the `gumbel` branch (GUMBEL.md:103-105).

**The Rust planner.** `native/` holds an event simulation of the same model and, on top of
it, beam search and MCTS (`cephdag/policies/native.py:1-5`). Python calls it through
`ctypes`, and only numeric buffers cross the boundary (`native/src/lib.rs:1`). The C
functions are:

- `planner_abi_version` (`lib.rs:54`), which must return 5, or Python refuses to load the
  library (`native.py:17, 35-39`);
- `planner_graph` and `planner_free` (`lib.rs:659, 679`);
- `planner_simulate` (`lib.rs:692`);
- `planner_search` for MCTS (`lib.rs:1032`);
- `planner_beam` (`native/src/alternatives.rs:39`).

The native policies support 1-12 nodes, each with both a worker and a pool, so they run in
cs mode only (`native.py:18`; README.md:77-79, 217-218). The planner is built inside the
image with the official Rust 1.85.0, hash-pinned (infra/README.md:226-231).

**Policies used in the demo:**

| policy | one line | file |
|---|---|---|
| data-local | Run where the fewest input bytes must move, keep outputs local, then balance load | `policies/baselines.py:48` |
| fifo-hash | The oldest ready task goes to the lowest-numbered free worker; outputs placed by hash, as CRUSH would. The disagg baseline | `policies/baselines.py:68` |
| greedy | Dispatch the action whose task is forecast to finish first | `policies/greedy.py:15` |
| greedy-wait | Greedy, but may wait for a busy worker that would finish sooner | `policies/greedy.py:39` |
| heft | One static HEFT list plan for the whole graph; transfers planned as if each had the link alone | `policies/list.py:76, 352`; SCHEDULERS.md:14-17 |
| heft-cc | HEFT that books every transfer on a send-link and a receive-link calendar; a placement that moves bytes must finish 5% earlier (`MOVE_MARGIN = 0.05`) | `policies/list.py:19, 296, 398`; SCHEDULERS.md:21-65 |
| stealing | Queue each task at its data-local worker; an idle worker steals it only if it would finish sooner | `policies/list.py:408` |
| backlog | Local tasks first; move a task off node s only while s's backlog outlasts the move | `policies/backlog.py:22`; SCHEDULERS.md:67-101 |
| lp-plan | A fluid LP over task classes, solved once (min T, then min bytes), and a dispatcher that keeps each worker on its planned mix | `policies/lp.py:24, 118`; SCHEDULERS.md:103-162 |
| lp-plan2 | lp-plan whose worker waits for a busy link when a move is due, and whose idle node takes back its own unstarted tasks | `policies/lp.py:228`; DIAGNOSIS.md section 2 |
| beam-N | Rust beam search over the next 12 dispatches and waits, keeping N states, 40 ms budget | `policies/native.py:216`; `native/src/alternatives.rs:39` |
| native-mcts-heuristic-N | Rust MCTS with N iterations and heuristic rollouts; kept as a comparison | `policies/native.py:257`; `native/src/lib.rs:1032` |
| Gumbel (gumbel-lb-N) | Gumbel-Top-k root sampling with Sequential Halving over a heuristic prior; simulation only, never run on the lab | branch `gumbel` `925abab`, `native/src/gumbel.rs:315`; GUMBEL.md:3-10, 101-130 |

The table's sources are README.md:59-75 and `policies/__init__.py:16-35`.

Two policies cannot run with two workers per container. backlog and lp-plan refuse cs2 and
disagg2, because they keep one queue or plan per host (`policies/backlog.py:6-10`).

## 5. Workloads

**Synthetic, independent tasks.**

- Data: 64 MiB float32 shards, one `map@1` task per shard, output ratio 0.25
  (`campaigns/demo3-slots.json`; `experiments/run.py:57-60`).
- Kernel: average the inputs, then repeat an elementwise multiply, add and tanh
  `iterations` times. Iterations therefore sets the arithmetic intensity
  (`cephdag/functions.py:68-97`).
- Chunking: the kernel runs on 1 MiB chunks (`CHUNK_VALUES = 2**18` floats). Iterating a
  whole 64 MiB array made workers on one host contend for the memory bus
  (`functions.py:14, 82-89`; PLAN.md:77-79).
- Placement: round-robin over nodes 0-7 (`placement [0..7]`) or skewed. The skewed list
  `[0,0,0,0,0,0,0,1,...,7]` puts 65 of 128 shards on node 0 and 9 on each other node
  (`campaigns/sched-skew.json`; SCHEDULERS.md:10-11).

**DROID pipeline** (README.md:131-169):

- Source: DROID 1.0.1 RLDS TFRecord shards. They are downloaded from public GCS to the HDD,
  each checked against its GCS MD5 (PLAN.md:68-70), then streamed into Ceph by
  `experiments/droid_ingest.py`.
- Reader: the TFRecord framing and `tf.train.Example` decoding are a small custom reader,
  checked against TensorFlow on real episodes. This lets ingest run in the lab's Python 3.9
  without TensorFlow (`droid_ingest.py:1-13`).
- Partitions: whole consecutive episodes, sized so that each task fits the worker's 512 MiB
  (about 32 MB of source at 128x128) (README.md:156-160).

Each partition feeds four functions (`cephdag/droid.py`; CPU-s per source GB measured on a
held-out shard, LAB-VALIDATION.md:135):

| stage | does | CPU-s per source GB |
|---|---|---|
| `decode_resize@1` | Decode every camera JPEG to 128x128 RGB; keep 14 state values per step. Frames are 2.16x the source | 16.06 |
| `encode_video@1` | Lossless FFV1 video per camera per episode, bit-exact | 22.78 |
| `frame_stats@1` | Exact per-frame channel sums and sums of squares | 11.20 |
| `state_parquet@1` | zstd Parquet of the state | 0.40 |

The frames feed both `encode_video` and `frame_stats`, so they are written once and read
twice (README.md:133-141; `experiments/droid_workload.py:1-7`).

The fused variant, `droid_fused@1`, runs all four on a partition in one task, keeps the
frames in memory and produces the same final outputs (`experiments/run.py:26-28`).

**Batches (waves).** About 450 GiB fits in Ceph at once, so the 496 GB run went in waves
(`campaigns/demo3-droid.json`: shards 0-549, 79 per wave, i.e. 7 batches). For each wave:

1. ingest it once;
2. run every configuration on it;
3. verify the outputs and delete the wave.

Each wave is a separate graph run, so only one wave's frames exist at a time. This also
pairs the cs and disagg runs against drift (PLAN.md:30-32; `droid_workload.py:1-7`;
README.md:165-169).

## 6. Experiment method

**Campaigns.** `experiments/campaign.py` runs on the host, outside the containers, driven by
a JSON spec under `experiments/campaigns/` (`campaign.py:1-47`):

- Synthetic campaigns create the dataset once, with shaping lifted for the bulk load
  (`bulk_mbit`). Each repeat runs every case in every mode, each mode with its own policies,
  cluster file and models. The mode order alternates between repeats, so a case's runs stay
  within one repeat. `once` limits an intensity to the first repeat, and `delete_at_end`
  removes the dataset at the end.
- Before each mode the driver switches and reshapes the lab. It runs a full `verify` on the
  first switch of a session (and again at intervals), and a light `check` otherwise.
- Every step's start, completion or failure goes to `ledger.jsonl`:
  - a completed step never runs again;
  - an interrupted step reruns as a new attempt in a new directory;
  - any failure stops the campaign until someone acknowledges it.
- HTTP calls to workers are retried up to 3 times (`coordinator.py:53`).
- Every run appends a row to `rows.jsonl`: mode, the digests of the cluster and models
  files, policy, wall and policy seconds, network bytes, and the verification
  (README.md:191-195).

**Repeats and statistics.**

- Synthetic headline campaigns ran 2-3 repeats; DROID ran one run per batch per
  configuration.
- Wall time is the median over repeats.
- A speedup is the baseline's best policy over cs's best policy within one repeat, then the
  median over repeats (DEMO-DATA.md:19-23, 53, 100).

**Calibration.** Each mode was calibrated before its runs. The disagg storage rates are taken
from the cs calibration, because a node without a worker cannot time its own disk
(README.md:202-216). Models for cs2 and disagg2 were derived from the 8-node calibrations by
giving each second worker its host's rates, not recalibrated (`experiments/derive_models.py:1-8`;
PLAN.md:126-127).

**Forecast first.** Before a new policy got lab time, its in-process forecast was written
down together with what would falsify it. Then the lab run was compared with the forecast:
lp-plan2 (PLAN.md:142-147), native MCTS (PLAN.md:165-170) and the network sweep
(PLAN.md:229-231). Across 47 policy-intensity cells, the forecast is within 2.5% of the lab
median (median absolute error) (DEMO-DATA.md:199-200). This is why the fair-sharing forecast
is used to screen planners before lab time (`~/projects/comp-storage/AGENTS.md:55`).

**Detached runs.** Campaigns ran as detached `runjob` jobs on aakashpc. Each job records its
manifest, log and exit code, and a scheduled watcher reports state changes
(`~/projects/AGENTS.md:37-45`).

- Chaining: stages were chained by gate scripts. Each waits for the previous stage to exit 0
  with its completion line in the log, and otherwise exits without touching the lab
  (PLAN.md:119-123).
- Disk guard: a guard stops the stages if free disk falls below a floor (PLAN.md:200-203).

## 7. Reused and custom

| part | origin |
|---|---|
| Ceph 20.2.4 (monitor, manager, OSD, RADOS and its Python bindings) | upstream; the lab image is built from the Ceph image (LAB-VALIDATION.md:8; infra/README.md:226-229; `cephdag/storage.py:35-53`) |
| numpy 2.0.2; PyAV, Pillow, PyArrow for DROID | upstream wheels, hash-locked (README.md:140-141, 225; infra/README.md:209-214) |
| tc tbf, IPIP, iperf3 | Linux and upstream tools (LAB-VALIDATION.md:17, 98; infra/README.md:117-120) |
| cephdag (coordinator, worker, model, policies, Rust planner, lab lifecycle, campaign driver) | custom. The first commit is the refinement-v2 lab of 2026-11-03/04, which came from the lab in Aakash's handoff archive; CLEANUP.md maps every original file (CLEANUP.md:1-6; `~/projects/comp-storage/AGENTS.md:76`) |
| TFRecord / `tf.train.Example` reader | custom, so ingest runs without TensorFlow in Python 3.9; checked against TensorFlow (`droid_ingest.py:11-13`) |
| Two-phase simplex for lp-plan | custom. scipy 1.9.3 in the containers does not import with numpy 2.0.2; tested against brute-force vertex enumeration on 300 random LPs (`policies/simplex.py:1-7`; SCHEDULERS.md:151-156) |

## 8. Limitations to say aloud

- **One host.** All 17 nodes are containers on one machine. They share its memory bus and
  SSD, and the network is emulated with tbf. Storage nodes have CPU quotas, not dedicated
  cores (DEMO-DATA.md:272-273; FAIRNESS.md:95-97). The OSDs also share one physical SSD, so
  per-node local I/O is not independent.
- **Host storage slowed during the weekend.** Across the DROID batches, local reads fell
  27-36% and writes 21% on every node at once. A day later the skew cells were still 9-30%
  slower. Compare numbers within a session, not across days (DIAGNOSIS.md:15-24;
  DEMO-DATA.md:55-56, 181-182).
- **Single replica.** Each pool has one copy, so an OSD failure stops its pool
  (infra/README.md:147-158).
- **Network rates.** Only 400, 1000 and 2500 Mbit/s were measured. At 2500 the router
  carried 2357 Mbit/s per node in the pre-run check; anything above 2.5 Gbit/s is untested
  (DEMO-DATA.md:255-273; PLAN.md:228-229).
- **Few repeats.** Synthetic campaigns ran 2-3 repeats, i4096 and the 256 GiB scale ran
  once, and each DROID batch ran once per configuration. At 1 iteration two repeats differ
  by up to 30% (DEMO-DATA.md:53-56, 123, 100, 274-276).
- **Single-slot harness.** There is no read-ahead or write-behind in either mode
  (FAIRNESS.md:55-58).
