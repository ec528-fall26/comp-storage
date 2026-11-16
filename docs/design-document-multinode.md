# Design document, part 2: many storage nodes and scheduling

*Demo 3, `demo-3` branch. Part 1, the slice API on one storage node, is
[design-document.md](design-document.md). The code for this part is in
[`cephdag/`](../cephdag); its results, raw rows and figures are in
[`demo3-cephdag/`](demo3-cephdag/README.md).*

## Reproduction guide

- Recreate the published charts using the included records and the [root README](../README.md#recreate-the-published-charts).
- Build the lab and collect new measurements with [section 3](#3-setup).
- Select a campaign, check its output, and resume an interrupted run with [section 4](#4-experiments-and-results).

## Why

Part 1 sliced DROID on the node that stores it. That moved 99.998% less data over the
network and took approximately the same time, 33.4 s in place and 33.7 s on separate
compute. With one storage node, a network of up to 12.5 Gbit/s and a slower disk,
avoiding the transfer produced little runtime improvement.

That leaves two questions.

1. When does computing next to the data save time, and how much?
2. Once the data is spread over many storage nodes, where should each task run?

To answer them we built a Ceph cluster of eight storage nodes that can also compute, a
baseline that computes away from the same data, and a scheduler interface with a dozen
policies behind it. What we found:

* **Computing at the storage saves what moving the data would have cost.** With equal
  workers on both sides it is 5.6x faster when tasks mostly read and write and 1.08x
  faster when they mostly compute. It is 5.7x faster on 400 Mbit/s links and 1.3x on
  2.5 Gbit/s links in the measured network sweep.
* **On 496 GB of DROID it is 3.76x faster,** 1.55 h against 5.85 h, and moves 6 GB over
  the network where the baseline moves 4.1 TB.
* **Uneven data needs a scheduler that counts link time.** When one node holds half the
  data, running every task where its data is overloads that node, and everything moved
  off it shares that node's one link. HEFT ignores the sharing and loses to simply running
  each task where its data is. HEFT with link time booked beats both, and a linear program
  solved once beats that.
* **On the real pipeline, planning has to get cheaper.** With DROID concentrated on one
  node, the linear program's schedule is the fastest to execute, but the 17 to 23 s spent
  computing it costs more than it saves. Running each task where its data is wins.

## 1. Architecture

### The lab

![Left: computational storage. Eight storage nodes, each with a worker beside its Ceph OSD, linked through a router at 400 Mbit/s. Right: the baseline. The same storage nodes without workers, and workers on eight compute-only nodes, so every input and output crosses a link.](demo3-cephdag/demo-data/arch_lab.png)

One host runs 17 Docker containers, described by
[`infra/labs/cephdag8.json`](../cephdag/infra/labs/cephdag8.json) and managed by
[`infra/lab.py`](../cephdag/infra/lab.py).

| Containers | Role | Each gets |
| --- | --- | --- |
| `cephdag8-n0..n7` | storage node: one Ceph OSD with its own pool, and a worker | 3 CPUs, 6 GiB |
| `cephdag8-c0..c7` | compute-only node: a worker and no storage | 2 CPUs, 3 GiB |
| `cephdag8-router` | carries every link, each shaped to 400 Mbit/s | 0.25 CPU |

Pool `node3` lives only on node 3's OSD, so an object's pool says which disk holds it.

One cluster runs both systems. Only the place where the workers run changes:

* **Computational storage** (`cs`): the workers run in the storage nodes.
* **Baseline** (`disagg`): the workers run in the compute-only nodes, so every input and
  every output crosses a link.

`cs2` and `disagg2` are the same two with two workers per node, 16 in all.

### The software

![A client submits a task graph and a policy name to the coordinator. At each event the coordinator asks the policy what to run next and sends that task to a worker, which reads its inputs from Ceph, computes, and writes its outputs to the pool it was told.](demo3-cephdag/demo-data/arch_software.png)

A client describes its work as a graph of tasks over stored partitions and submits it
with the name of a scheduling policy:

```python
from cephdag import Client, Cluster, Graph

client = Client(
    Cluster.load("cephdag/clusters/cephdag8-cs.json"),
    models="/results/calibration/cephdag8-cs/models.json",
)
data = client.dataset("my-dataset")

graph = Graph()
mapped = graph.map("map@1", data, iterations=32, ratios=[0.5])
graph.reduce("join@1", mapped, iterations=1, ratios=[0.5])

job = client.submit(graph, "heft-cc")
job.wait(timeout=600)
for ref in job.outputs():
    print(ref.pool, ref.key)
```

`map@1` and `join@1` are registered functions. `iterations` sets how much compute each
task does and `ratios` how large each output is relative to its input. The scheduler
uses both.

What happens after `submit`:

1. **The coordinator** ([`coordinator.py`](../cephdag/cephdag/coordinator.py), in node 0)
   knows where every stored partition and every finished output is, and what each worker
   is doing. At every event it asks the policy what to do next.
2. **A policy** is one method, `choose(state)`. It returns
   `Dispatch(task, worker, targets)`: run this task on that worker and store output *i*
   on node `targets[i]`. Or it returns `None`, which means wait. Where a task runs and
   where its outputs go are separate choices.
3. **A worker** ([`worker.py`](../cephdag/cephdag/worker.py)) reads its inputs from Ceph
   and checks their checksums, runs the function, writes each output to the pool it was
   given, and publishes one completion record.
4. **Verification.** After the last task every final output is read back and checked, and
   one digest of all outputs is recorded. Every policy and both modes must produce the
   same digest.

The policies ([`cephdag/policies/`](../cephdag/cephdag/policies)):

| Policy | Rule |
| --- | --- |
| `fifo-hash` | The baseline's. Oldest ready task to the first free worker; outputs placed by a hash of their name, as Ceph would. |
| `data-local` | Run the task where the fewest input bytes have to move, and keep its outputs there. |
| `heft` | HEFT list scheduling. It plans each transfer as if it had the link to itself. |
| `heft-cc` | HEFT that books time on each node's send and receive link, and moves a task only if that finishes it 5% sooner. |
| `lp-plan2` | Solves one linear program for how many tasks of each kind each node should take, then follows that plan. |
| `backlog`, `stealing`, `greedy`, `greedy-wait` | Cheap rules for moving work off a busy node. |
| `beam-N`, `native-mcts-heuristic-N` | Lookahead search in a Rust simulator ([`native/`](../cephdag/native)). |

### The workloads

* **Synthetic.** One task per 64 MiB shard. The function repeats a multiply, an add and a
  tanh over the shard `iterations` times, so one number sets how much compute each byte
  gets. Shards are spread evenly, or skewed so that node 0 holds 65 of 128.
* **DROID.** The preprocessing pipeline on DROID 1.0.1: decode every camera frame and
  resize it to 128x128, encode lossless video, compute per-frame statistics, and write
  the robot state to Parquet. The resized frames are 2.16x the size of the source and two
  later tasks read them. A fused variant does all four steps in one task and never stores
  the frames.

## 2. Design decisions

**Containers on one host.** The proposal planned Rook. We needed
16 nodes on one Ceph cluster, a baseline on identical data, and room for 500 GB of DROID.
Docker on one large machine gave us that under one script. The price is that all the
nodes share that machine (section 5).

**The baseline uses the same storage cluster.** Both modes use the same data, OSDs,
and link configuration. Each active worker container receives a 2-CPU, 3-GiB worker
budget. Storage containers also receive a 1-CPU, 3-GiB OSD budget. Docker enforces the
combined container limit, so the worker and OSD share it in combined mode. The
intensity and network plots compare combined HEFT with separate FIFO-hash. The
large DROID comparison uses combined data-local and separate HEFT.

**Equal worker counts within each comparison.** The intensity and network sweeps
use 16 workers per mode. The 496 GB DROID, 256 GiB scale, and skewed scheduling
experiments use eight per mode. An earlier eight-versus-sixteen comparison allowed
worker count to influence the result, including a 0.72x speedup at 4096 iterations.

**One pool per node, one copy of the data.** "Local" then means the node's own disk, and
the scheduler knows where every byte is. Ceph's default placement would scatter each
dataset over all the nodes.

**Links at 400 Mbit/s.** A node reads its own disk at 264 to 392 MB/s and a link carries
47 MB/s, so moving data is the expensive step, as it is for a large dataset on a shared
network. Experiment 2 raises the links to 2.5 Gbit/s to show what happens when it is not.

**Planning time counts.** The coordinator dispatches nothing while a policy is thinking,
so a policy is judged on wall time with its own planning included.

**Book the links (`heft-cc`).** HEFT assumes every transfer has its link alone. On the
skewed data it planned 31 moves off node 0 and a finish at 11.5 s; node 0's one link
needed 44 s to carry them. `heft-cc` reserves time on the sending and the receiving link
for every transfer, so its plan never overlaps two moves out of one node.

**Plan once (`lp-plan2`).** A linear program decides how many tasks each node takes from
each other node, first for the earliest finish and then for the fewest bytes moved. It is
solved once per job, in about 50 ms on the synthetic graphs, and a dispatcher follows it.
The dispatcher holds a worker for a busy link when its next task is a planned move, and
lets a node that has finished early take back tasks stored on it.

**Predict before running.** Candidate policies were evaluated in a simulator that
shares each disk and link fairly among the transfers using it. Over 47 combinations of policy and
workload its median error against the lab was 2.5%, and 42 were within 10%. A policy got
lab time only with its prediction written down first.

## 3. Setup

To recreate the published tables from the included records, use the commands in the
[root README](../README.md#recreate-the-published-charts). The steps below run new
measurements on a Linux host. Complete them in order, in the same Bash session.

### Local tests

The tests run with in-process storage and workers. From the repository root, with
Python 3.9 and Rust installed

```bash
cd cephdag
python3.9 -m venv .venv
.venv/bin/python -m pip install numpy==2.0.2
.venv/bin/python -m pip install -r requirements-droid.txt
cargo build --release --offline --manifest-path native/Cargo.toml
.venv/bin/python -m unittest discover -s tests -t .
```

Expect 263 tests, with three skipped when the full DROID shard is absent. Set
`CEPHDAG_DROID_SHARD` to the path of shard 00217 to include those three. The small
DROID fixture used by the remaining tests is already in `tests/fixtures/`.

### 3.1. Prepare the host and paths

The measured host had 32 logical CPUs and 123 GiB of RAM. The supplied configuration
reserves about 72 GiB for containers and another 8 GiB for the host. Its eight OSDs
have 448 GiB of logical capacity. The budget check also requires 80 GiB free beyond
that on Docker's disk. Our btrfs-backed volumes grew to about 700 GiB during the full
campaign, so actual physical use can exceed the logical OSD capacity.

Use an x86-64 Linux host with Python 3.9 or newer, pip, curl, Docker Engine, and the
Docker Compose plugin. The kernel must support `ipip` and `sch_tbf`. Docker's data
root holds the OSD volumes. `DEMO_RESULTS` below holds raw DROID, calibration, and
experiment records and needs about 510 GB for the full DROID experiments.

Start in `comp-storage/cephdag`. If starting from a fresh shell at the repository
root, run `cd cephdag` first. Choose an absolute path on the disk that will hold the
raw data and results, then create local copies of the configuration files

```bash
export DEMO_RESULTS="$HOME/cephdag-demo3-results"
export DEMO_LAB="state/reproduce/cephdag8.json"
export DEMO_CAMPAIGNS="state/reproduce/campaigns"
mkdir -p "$DEMO_RESULTS" "$DEMO_CAMPAIGNS"

python3 - <<'PY'
import json
import os
from pathlib import Path

results = str(Path(os.environ["DEMO_RESULTS"]).resolve())
lab_path = Path(os.environ["DEMO_LAB"])
lab = json.loads(Path("infra/labs/cephdag8.json").read_text())
lab["results_dir"] = results
lab_path.write_text(json.dumps(lab, indent=2) + "\n")

for source in Path("experiments/campaigns").glob("*.json"):
    spec = json.loads(source.read_text())
    spec["lab"] = str(lab_path)
    spec["results_host"] = results
    target = Path(os.environ["DEMO_CAMPAIGNS"]) / source.name
    target.write_text(json.dumps(spec, indent=2) + "\n")
PY

python3 infra/lab.py --lab "$DEMO_LAB" plan
```

A successful plan ends with `Configuration passes isolation and budget checks.`
Review any `REFUSED` lines before continuing. `up` also checks Docker's actual data
root and existing resources. The lab name is `cephdag8`, and its network subnets are
`172.30.144.0/24` through `172.30.159.0/24`.

Both `results_dir` in the lab config and `results_host` in each campaign must point
to the same host directory. Inside containers, that directory is mounted at
`/results`. The script above sets both sides of that mapping. Run it once for a fresh
experiment workspace.

### 3.2. Build and start the lab

The image pins Ceph by digest, installs Python wheels with checked hashes, and builds
the native scheduler with Rust 1.85.0. Download its build inputs from `cephdag/`

```bash
mkdir -p deps/wheels deps/rust
python3 -m pip download --require-hashes --no-deps --only-binary=:all: \
    --dest deps/wheels --python-version 3.9 --implementation cp --abi cp39 \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
    -r infra/requirements-linux-amd64.lock \
    -r infra/requirements-droid-linux-amd64.lock

for component in rustc rust-std cargo; do
    curl -fL \
        "https://static.rust-lang.org/dist/${component}-1.85.0-x86_64-unknown-linux-gnu.tar.xz" \
        -o "deps/rust/${component}-1.85.0-x86_64-unknown-linux-gnu.tar.xz"
done

mkdir -p state/cephdag8
printf '%s\n' '{"approved": true}' > state/cephdag8/approval.json
python3 infra/lab.py --lab "$DEMO_LAB" build
python3 infra/lab.py --lab "$DEMO_LAB" up
python3 infra/lab.py --lab "$DEMO_LAB" bootstrap
python3 infra/lab.py --lab "$DEMO_LAB" cluster
python3 infra/lab.py --lab "$DEMO_LAB" mode cs --functions cephdag.droid
python3 infra/lab.py --lab "$DEMO_LAB" shape 400
python3 infra/lab.py --lab "$DEMO_LAB" verify cs
```

The approval file enables this lab's deployment commands. `up` creates 17 containers,
their networks, and eight named OSD volumes. `bootstrap` initializes those volumes
once. Successful verification writes
`results/deployment/cephdag8/validated-cs.json`, including pool placement, checksum
checks, and the measured shared-link rates.

### 3.3. Download the calibration shard and calibrate

First download the held-out calibration shard. This is sufficient to calibrate the
lab and run the synthetic campaigns

```bash
python3 ../docs/demo3-cephdag/droid_download.py "$DEMO_RESULTS/droid/raw" \
    --bytes 0 --also 2047

docker exec cephdag8-n0 python3 -m experiments.droid_ingest \
    --shards 2047 --resolution 128 --placement 0,1,2,3,4,5,6,7 \
    --source-dir /results/droid/raw --cluster cephdag/clusters/cephdag8-cs.json
```

Read the measured link rate from verification and fit the combined-mode models

```bash
CS_RATE=$(python3 -c 'import json; d=json.load(open("results/deployment/cephdag8/validated-cs.json")); print(min(d["aggregate_mbit"].values()))')
docker exec cephdag8-n0 python3 -m experiments.calibrate \
    --network-mbit "$CS_RATE" --droid droid-128-02047 \
    --cluster cephdag/clusters/cephdag8-cs.json \
    --output /results/calibration/cephdag8-cs

python3 infra/lab.py --lab "$DEMO_LAB" mode disagg --functions cephdag.droid
python3 infra/lab.py --lab "$DEMO_LAB" shape 400
python3 infra/lab.py --lab "$DEMO_LAB" verify disagg

DISAGG_RATE=$(python3 -c 'import json; d=json.load(open("results/deployment/cephdag8/validated-disagg.json")); print(min(d["aggregate_mbit"].values()))')
docker exec cephdag8-n0 python3 -m experiments.calibrate \
    --network-mbit "$DISAGG_RATE" --droid droid-128-02047 \
    --cluster cephdag/clusters/cephdag8-disagg.json \
    --storage-from /results/calibration/cephdag8-cs/calibration.json \
    --output /results/calibration/cephdag8-disagg
```

The separate-mode calibration reuses the measured storage rates and measures its own
workers. The original link rate was about 377 Mbit/s after shaping to 400 Mbit/s.
Calibration writes `calibration.json` and `models.json` under each mode's directory
in `$DEMO_RESULTS/calibration/`.

Derive the models for two workers per node and for fused DROID

```bash
for mode in cs disagg; do
    base="/results/calibration/cephdag8-$mode/models.json"
    docker exec cephdag8-n0 python3 -m experiments.derive_models "$base" \
        "/results/derived-models/cephdag8-${mode}2/models.json" \
        --cluster "cephdag/clusters/cephdag8-${mode}2.json" --fused
    docker exec cephdag8-n0 python3 -m experiments.derive_models "$base" \
        "/results/derived-models/cephdag8-$mode-fused/models.json" --fused
done
```

Run derivation once after calibration. It uses exclusive file creation, so an existing
output means that a model is already present. Preserve it with the results it produced
before generating models for a new calibration.

### 3.4. Download inputs for the DROID experiments

The small fused-pipeline comparison uses three additional shards

```bash
python3 ../docs/demo3-cephdag/droid_download.py "$DEMO_RESULTS/droid/raw" \
    --bytes 0 --also 550 551 552
```

For the 496 GB experiment and the skewed DROID experiment, download the full set used
for Demo 3

```bash
python3 ../docs/demo3-cephdag/droid_download.py "$DEMO_RESULTS/droid/raw" \
    --bytes 500e9 --also 2047
```

For the supplied release, this selects shards 0–552 plus 2047, about 501.5 GB. New
downloads are checked against the published MD5. On resume, existing files are reused
when their sizes match. Campaign ingest checks source checksums again.

### 3.5. Stop and resume

Run campaigns one at a time because they share the same cluster and change its active
workers and link rates. Between campaigns, stop the lab with

```bash
python3 infra/lab.py --lab "$DEMO_LAB" stop
```

To use the existing lab again, run `resume`, then let the next campaign select its mode

```bash
python3 infra/lab.py --lab "$DEMO_LAB" resume
```

These commands preserve the named volumes and stored data. After opening a new shell,
return to `cephdag/` and set the three `DEMO_...` variables from section 3.1 to the same
values before continuing.

## 4. Experiments and results

Run from `comp-storage/cephdag` after section 3. Start with the worker-matched
synthetic sweep

```bash
export DEMO_SPEC="demo3-slots"
python3 experiments/campaign.py plan "$DEMO_CAMPAIGNS/$DEMO_SPEC.json"
python3 experiments/campaign.py run "$DEMO_CAMPAIGNS/$DEMO_SPEC.json"
```

`plan` prints steps, estimated duration, disk use, and any `problem:` lines. Resolve
those lines before running. A completed run prints `demo3-slots: complete`. This
campaign produces 27 result rows. It took about 1.4 hours on the original host,
excluding image setup and calibration.

For another experiment, change `DEMO_SPEC` to a name below and use the same two
commands. The driver selects worker locations, shapes the links, creates or ingests
data, verifies outputs, and removes the campaign's temporary datasets when finished.

| Question | Campaign spec name | Inputs | Expected result rows |
| --- | --- | --- | --- |
| How does compute intensity change the benefit? | `demo3-slots` | 16 GiB synthetic, 16 workers per mode | 27 |
| How does network speed change it? | `netsweep-400`, `netsweep-1000`, `netsweep-2500` | 8 GiB synthetic, 16 workers per mode | 8 each |
| Does it hold at larger scale? | `demo3-scale` | 256 GiB synthetic, 8 workers per mode | 6 |
| How much does the real pipeline save? | `demo3-droid` | DROID shards 0–549, 8 workers per mode | 21 |
| What changes when stages are fused? | `demo3-slots-droid` | DROID shards 550–552, 8 or 16 workers according to mode | 6 |
| Which policy handles uneven data? | `sched-skew` | 8 GiB synthetic, 8 workers | 96 |
| Does the revised LP dispatcher help? | `sched-lp`, `sched-lp-low`, `sched-lp-uniform` | Synthetic skew and uniform placement, 8 workers | 18, 24, 6 |
| Does planning pay on the real pipeline? | `sched-droid` | DROID shards 500–515, fused, 8 workers per mode | 10 |
| Does MCTS help? | `sched-mcts` | 8 GiB synthetic, 8 workers | 24 |

The 496 GB DROID campaign took about 16.7 hours including its policies, ingest,
verification, and mode changes. Its 1.55-hour and 5.85-hour headline times sum the
measured graph execution times for the compared policies across seven batches.

### Read and verify a campaign

Results are under `$DEMO_RESULTS/campaigns/$DEMO_SPEC/`.

| File | What to inspect |
| --- | --- |
| `driver.log` | Progress and command output |
| `ledger.jsonl` | Started, completed, and failed steps, including retry attempts |
| `<step>/a<attempt>/rows.jsonl` | One summary row per policy run |
| `<step>/a<attempt>/run-*.json` | Full task timings and execution details for synthetic runs |
| `<step>/a<attempt>/wave-*/run-*.json` | Full task timings for DROID waves |

This prints each completed policy run and checks its recorded verification flag

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["DEMO_RESULTS"]) / "campaigns" / os.environ["DEMO_SPEC"]
for path in sorted(root.rglob("rows.jsonl")):
    for line in path.read_text().splitlines():
        row = json.loads(line)
        assert row["verification"]["passed"], path
        moved_gb = row["network_payload_bytes"]["bytes"] / 1e9
        print(path.parent.relative_to(root), row["mode"], row["policy"],
              f"{row['wall_seconds']:.2f} s", f"{moved_gb:.3f} GB")
PY
```

`wall_seconds` includes scheduling time, which is also reported as `policy_seconds`.
The plotted network metric is `network_payload_bytes.bytes`, computed from the
objects transferred between nodes. Interface-counter measurements are recorded
separately. Successful campaign completion also requires matching output digests
across policies and modes for each matching input case. Different datasets have
different digests.

A rerun of the same command resumes completed steps from its ledger. If a step failed,
read the recorded reason, correct the cause, and resume with

```bash
python3 experiments/campaign.py run "$DEMO_CAMPAIGNS/$DEMO_SPEC.json" \
    --acknowledge-failure
```

A fresh measurement needs a new campaign `name` in a copy of the spec, so it gets a
new result directory. Keep the existing records when recalibrating or changing a
spec. A retry is recorded as a new attempt.

### Compare with the reported results

The tables below come from the included records. All 422 runs used by the chart pack
passed verification. Times are medians over repeats unless a result explicitly sums
batches. Speedups are computed within a repeat and then summarized, so a displayed
speedup can differ slightly from the ratio of the two displayed median times.

The chart generator in the root README expects the complete set of campaigns in the
supplied archive. For a single fresh campaign, inspect its rows as above. Its new
measurements may differ with hardware, calibration, and host load. Preserve worker
counts, workload parameters, and policy names when comparing with the tables.

### Experiment 1. How much it saves depends on how much the task computes

`demo3-slots.json`, 1.4 h. 16 GiB in 256 tasks, spread evenly, 16 workers on each side.
The values below compare combined HEFT with separate FIFO-hash. Data-local is also
measured in the same campaign.

| Iterations | Computational storage | Baseline | Speedup |
| ---: | ---: | ---: | ---: |
| 1 | 11.6 s | 64.2 s | 5.60x |
| 64 | 12.9 s | 65.8 s | 5.13x |
| 256 | 31.1 s | 71.8 s | 2.36x |
| 1024 | 95.0 s | 118.9 s | 1.25x |
| 4096 | 362.6 s | 392.4 s | 1.08x |

The baseline moves 21.5 GB in every run and computational storage at most 0.27 GB. At 1
iteration a baseline task reads for 2.34 s, computes for 0.02 s and writes for 0.55 s.
The same task at the storage takes 0.27, 0.07 and 0.08 s. As the compute grows, the
minute the baseline spends moving data becomes a smaller share of the run.

At 256 GiB, 16 times the data, the speedups are 4.87x at 1 iteration and 2.21x at 256
(`demo3-scale.json`, 4.1 h, 8 workers on each side, one run each).

![Speedup against iterations on a log scale. Solid: 16 workers on each side, from 5.6x at 1 iteration to 1.08x at 4096. Dashed: 8 on each side, a day earlier. Neither crosses 1x.](demo3-cephdag/demo-data/fig1_speedup_vs_intensity.png)

### Experiment 2. It also depends on the network

`netsweep-1000.json`, `netsweep-2500.json`, `netsweep-400.json`, 28 min together. 8 GiB,
16 workers on each side.

| Link speed | Speedup at 1 iteration | At 256 iterations |
| ---: | ---: | ---: |
| 400 Mbit/s | 5.67x | 2.19x |
| 1000 Mbit/s | 2.44x | 1.39x |
| 2500 Mbit/s | 1.27x | 1.14x |

Computational storage takes 5.5 to 6.0 s at every speed, because it sends nothing. The
baseline falls from 32.1 s to 7.5 s. Before the run we predicted 5.6x, 2.5x and 1.4x.

At 2.5 Gbit/s a link carries about 300 MB/s, as much as a node reads from its own disk,
and the advantage is almost gone. Part 1's AWS testbed, with a faster network and a
250 MB/s disk, is past that point, which is why its two runs took the same time.

![Speedup against link speed: 5.67x, 2.44x and 1.27x at 1 iteration, next to the prediction made before the run.](demo3-cephdag/demo-data/fig6_network_speed.png)

### Experiment 3. DROID at 496 GB

`demo3-droid.json`, 16.7 h. Shards 0 to 549, 64,144 tasks, in 7 batches of about 70 GB
because the cluster holds 448 GiB. 8 workers on each side.

| | Computational storage | Baseline |
| --- | ---: | ---: |
| Wall time | 5,595 s (1.55 h) | 21,059 s (5.85 h) |
| Moved over the network | 6.2 GB | 4,107 GB |

That is 3.76x. Most of it comes from the stored frames, which the baseline writes once
and reads twice across the network. With the fused pipeline, which never stores them, the
speedup on three shards is 1.79x where the unfused pipeline gives 4.30x
(`demo3-slots-droid.json`, 13 min, one run each).

![Wall time per DROID batch: about 13 minutes with computational storage and 46 to 53 minutes for the baseline.](demo3-cephdag/demo-data/fig2_droid_batches.png)

### Experiment 4. Uneven data needs a scheduler that counts link time

`sched-skew.json`, 2.4 h; `sched-lp.json` and `sched-lp-low.json`, 1.0 h. Computational
storage only. 8 GiB with 65 of 128 shards on node 0, 3 repeats. Seconds:

| Policy | 1 iteration | 16 | 128 | 1024 |
| --- | ---: | ---: | ---: | ---: |
| `heft` | 45.5 | 39.2 | 56.4 | 108.1 |
| `data-local` | 23.2 | 22.5 | 39.3 | 100.1 |
| `heft-cc` | 18.3 | 20.2 | 35.1 | 95.4 |
| `lp-plan2` | 12.8 | 16.0 | 33.4 | 88.6 |

`lp-plan2` ran in later sessions, in which `heft-cc` took 18.3, 19.0, 35.7 and 95.0 s.
It was the fastest policy in every repeat and moved 0.5 to 3.0 GB where `heft-cc` moved
2.4 to 4.5 GB.

On evenly spread data `heft-cc` and `lp-plan2` move almost nothing and tie `data-local`,
while `heft` moves 5 to 8 GB and runs 17 to 37% slower (`sched-uniform.json`,
`sched-lp-uniform.json`).

![Wall time relative to the best policy at each intensity on skewed data. heft-cc is at or near 1.0 throughout; heft is the worst at 1, 16 and 128 iterations.](demo3-cephdag/demo-data/fig4_skew_policies.png)

### Experiment 5. On the real pipeline, planning time eats the gain

`sched-droid.json`, 0.6 h. Fused DROID, shards 500 to 515 in two batches, with node 0
holding 54% and 62% of the bytes. 8 workers on each side, one run per batch.

| | Batch 1 | Batch 2 | Of which planning |
| --- | ---: | ---: | ---: |
| `data-local` | 73.8 s | 78.0 s | 0 s |
| `heft-cc` | 85.6 s | 87.6 s | 8 s |
| `lp-plan2` | 86.7 s | 99.7 s | 17 s, 23 s |
| `heft` | 87.9 s | 85.9 s | 1 s |
| Baseline | 117.6 s | 116.1 s | 0 s |

Without its planning time `lp-plan2`'s schedule is the fastest (69.3 and 76.6 s) and
moves the fewest bytes. It plans slowly here because every DROID partition has its own
size, which gives the linear program one group of variables per task, 217 and 224, where
the synthetic graph has 8. Grouping partitions by size would shrink it. We have not done
that.

![Wall time per batch of fused DROID for each policy and the baseline. The hatched part of each bar is planning, largest for lp-plan2.](demo3-cephdag/demo-data/fig7_sched_droid.png)

### Experiment 6. Search overhead exceeds its measured benefit

`sched-mcts.json`, 0.7 h. On the skewed data, MCTS with 128 iterations per decision was
49% and 44% slower than `heft-cc` at 1 and 128 iterations (26.4 against 17.7 s, 53.5
against 37.1 s) and moved the most data of any policy. At 1024 iterations the two are
within the drift between runs. Gumbel MCTS, tested in the simulator with a perfect model
of the cluster, did no better than random restarts with the same budget, so it did not
get lab time ([GUMBEL.md](demo3-cephdag/GUMBEL.md)).

## 5. Limitations

* **One machine.** All 16 nodes and the router are containers on one host. They share
  its memory and one SSD, and the links are emulated. Real disks and a real network
  would differ.
* **Deployment uses Docker.** Moving the lab to the proposed Kubernetes and Rook
  deployment remains future work.
* **One copy of the data.** Losing an OSD loses its pool.
* **Few repeats.** Two or three per synthetic case. The 4096-iteration case, the 256 GiB
  runs and each DROID batch ran once.
* **The host's storage slowed over the weekend,** by 9 to 30% between days. Numbers are
  compared within one campaign, never across days.
* **Tasks and outputs are placed; inputs are not.** Data is placed once when it is
  loaded. The proposal's idea of placing related inputs together is not built.
* **Two systems.** Part 1's slice API does not submit to this scheduler, and this
  cluster has run neither the slice nor the Demo 2 conversion.
