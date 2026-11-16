# cephdag

cephdag runs a static task graph on a small cluster in which every machine both stores
data and computes on it. It exists to answer one research question: when compute
placement and the storage placement of each output are separate decisions, and waiting
is allowed, does planning ahead beat cheap locality and greedy rules once the planning
time itself is counted? The objective is wall time, planning included; network bytes
break ties.

This repository is the cleaned-up version of the refinement-v2 lab. It keeps the same
scheduling decisions (checked against a recorded fixture, see Tests) and adds a general
client interface. It has not yet been run on Ceph.

## Nodes

A node is one machine (in the lab, one Docker container) with two parts. Its Ceph OSD
owns a single-copy RADOS pool, so an object written to `node1` really lives on node 1's
disk. Its worker runs one task at a time over HTTP. `cephdag/lab-cluster.json` lists the
three lab nodes: each node's pool and its worker's URL. Another cluster is just another
JSON file of the same shape. `infra/labs/` describes labs with other node counts, some
keeping a second copy of each pool on the next node; their cluster files are in
`cephdag/clusters/`.

A node may also have only one of the two parts: `"worker": null` makes it storage-only,
`"pool": null` compute-only. Such a disaggregated cluster is the conventional baseline
to computational storage: tasks run only on nodes with a worker, outputs go only to
nodes with a pool, so every input and output crosses the network. The metadata pool must
be on a node with a pool, and a compute-only worker keeps its job records there.

## The life of one task

The coordinator sends a task to a worker together with the stored location of every
input. The worker reads each input from RADOS and checks its checksum, runs the
registered function, and writes each output to the pool of the node the coordinator
chose for it. Then it publishes one completion record. That record is the only proof
that the task finished; if a write fails, no record appears.

The coordinator polls the worker, sees the completion, and acknowledges it with a token.
Only after that acknowledgment, and after reading the durable record back, does the
worker drop the job from memory. A retried request gets the identical answer from RADOS
instead of running the task twice. This acknowledgment protocol is what fixed the
out-of-memory failure of the first refinement attempt.

## What the coordinator decides

The coordinator runs inside node 0. At every event (a task or one of its read, compute or
write stages starting or finishing) it rebuilds a picture of the cluster: which tasks are
done and where their outputs are, how far each running stage has got, and which tasks
are ready. That picture is a `model.Simulation`, the same object the policies use to
forecast the future: it plays reads, computation and writes forward, sharing disk and
network bandwidth among everything running at once.

The coordinator then calls `policy.choose(state)`. A policy answers with
`Dispatch(task, worker, targets)`: run this task on that worker and store output i on
node `targets[i]`. Or it answers `None`, which means wait for the next event. Completed
outputs enter the forecast with their real stored sizes; outputs not yet produced use
the sizes declared in the graph.

## Policies

- `random`: a random ready task, free worker and placement. A floor.
- `single-node`: everything runs and is stored on node 0.
- `fifo-hash`: a conventional system that ignores locality: the oldest ready task goes to
  the lowest-numbered free worker, and each output to the pool a stable hash of its name
  picks, as CRUSH does.
- `data-local`: run where the fewest input bytes must move; keep outputs local (on a
  compute-only worker, which has nowhere local, outputs go to the first pool).
- `greedy`: dispatch the action whose task is forecast to finish first.
- `greedy-wait`: like greedy, but may wait for a busy worker that would finish sooner.
- `heft`: one static HEFT list plan for the whole graph, followed as workers free up.
- `stealing`: queue each task at its data-local worker; an idle worker steals it only if
  it would finish sooner.
- `beam-<width>`: beam search over the next actions, in Rust (`native/`).
- `native-mcts-heuristic-<n>`, `native-mcts-uniform-<n>`: Monte Carlo tree search in
  Rust, kept as a comparison.

The two Rust policies support 1 to 12 nodes, each with both a worker and a pool; the
others work for any node count and on disaggregated clusters (`single-node` only if its
node has both parts).

## The client

```python
from cephdag import Client, Cluster, Graph

client = Client(Cluster.load("cephdag/lab-cluster.json"), models="results/models.json")
data = client.put_dataset("readings", parts)   # one bytes object per partition, round-robin
data = client.dataset("readings")              # or open an existing one

graph = Graph()
mapped = graph.map("map@1", data, iterations=32, ratios=[0.5])
graph.reduce("join@1", mapped, iterations=1, ratios=[0.5])

job = client.submit(graph, "heft")             # a policy name, or any object with choose()
job.wait(timeout=600)
print(job.status(), job.metrics()["wall_seconds"])
for ref in job.outputs():
    print(len(client.read(ref)))
```

`put_dataset` takes `placement=[node, ...]` to choose where each part goes. `submit`
passes extra keyword arguments to the policy, for example `seed=7` or `budget_ms=40`.

A function is defined once, with its signature and implementation together, and the same
module is imported by the client and by every worker:

```python
from cephdag import function

@function("grep", "1", inputs=["lines"], outputs=["lines"])
def keep_matching_lines(inputs, params, output_sizes):
    keyword = params["keyword"].encode()
    lines = inputs[0].splitlines(keepends=True)
    return (b"".join(line for line in lines if keyword in line),)
```

Inputs and outputs are bytes; the type tags are labels the graph checks for agreement.
`ratios` gives each output's size as a multiple of the input size. For a function like
this one that is only an estimate for the planner: the worker records the real size, and
later decisions use it. The worker still refuses a task whose inputs and actual outputs
would exceed its 512 MiB memory bound. `iterations` is a cost hint the compute model
multiplies by the input size. Anything else goes in `params`. A worker loads extra
function modules with `--functions`. `examples/custom_function.py` is a complete keyword
count.

The builtin `map@1`, `join@1` and `split@1` on the three-node lab send byte-for-byte the
requests the deployed v2 workers accept, so the new coordinator can be tried against the
running lab without redeploying them. Custom functions, type tags other than `float32`,
sizes that differ from the declared ratios, and other node counts need the new worker.

## The DROID pipeline

`cephdag/droid.py` registers the October DROID pipeline (`droid-dag-v1`) as four
functions over partitions of whole consecutive episodes. `decode_resize@1` decodes every
camera JPEG, resizes it bilinearly to R×R RGB and keeps the 14 float64 state values per
step (7 joint positions, 7 action values); its frames feed both `encode_video@1` (lossless
FFV1 in Matroska, one video per camera per episode, bit-exact so that every policy writes
identical bytes) and `frame_stats@1` (exact per-frame channel sums and sums of squares);
its state feeds `state_parquet@1` (zstd Parquet with episode, step and instruction
columns). Workers load the module with `--functions cephdag.droid` and need the pins in
`requirements-droid.txt` (PyAV, Pillow, PyArrow; cp39 manylinux wheels).

```sh
python -m experiments.droid_ingest --shards 0-63 --resolution 128 --placement 0,0,1,2
python -m experiments.calibrate --network-mbit 400 --droid droid-128-02047
python -m experiments.run --workload droid --droid-shards 0-63 --resolution 128 \
    --wave-gib 16 --policies greedy,heft,stealing --repeats 1 \
    --models results/models.json --output results/droid
```

The ingest streams DROID 1.0.1 RLDS shards from public GCS into the cluster through
`Client.put_dataset`, one dataset per shard (`droid-128-00017`), and checks each stream
against the MD5 that GCS publishes; nothing is staged on disk. It needs no TensorFlow:
the TFRecord framing and `tf.train.Example` decoding are a small custom reader, checked
against TensorFlow on every episode of a real shard (`tests/fixtures/droid-digests.json`).
A partition object keeps the source JPEGs, so it is about the size of the source; it
holds as many episodes as let every task fit the worker's 512 MiB reservation at the
chosen resolution (about 32 MB of source at 128×128), and an episode too long for that
is split into step ranges. `datasets/<name>/droid.json` records the source shard, its
MD5, and each partition's episodes, step ranges and bytes.

Each function has its own compute cost in `models.json` (`compute.functions`: fixed,
per input byte, per output byte), fitted by `calibrate --droid` on a shard that no
workload uses; every other function keeps the single model, so the builtins forecast
exactly as before. `experiments.run` runs the shards as waves of at most `--wave-gib`
source each, every wave a separate graph run under every policy; it verifies each wave
(videos decoded back against the frames, sampled statistics recomputed, Parquet read
back against the state, sampled frames re-derived from the source JPEGs) and then removes
that wave's outputs, so only one wave's frames exist at a time.

## Running the lab

The lab itself is created and checked by `infra/lab.py` (Docker, Ceph, shaped network);
see `infra/README.md`. Start a worker on each node (`--cluster` names the cluster file of
other labs), then calibrate, run and summarize from node 0:

```sh
python3 -m cephdag.worker --node 0          # on each node, with its own number
python3 -m experiments.calibrate --network-mbit 400
python3 -m experiments.run --workload independent --iterations 1 --ratio 0.25 \
    --policies greedy,greedy-wait,heft,stealing,beam-4 --repeats 3 \
    --models results/models.json --output results/independent
python3 -m experiments.summarize results/independent
```

`experiments.run` reads every final output back against its checksum, requires all
policies to produce identical outputs, recomputes sampled values with an independent
scalar oracle, refuses to start when the Docker disk is short of space, and afterwards
deletes only the payloads that run generated. Source datasets are opened by name and never
removed by a run; `experiments.datasets` creates a synthetic one ahead of the runs and
deletes datasets. Each run appends a line to `<output>/rows.jsonl`: mode (cs or disagg),
the cluster and models files' SHA-256, policy, workload, iterations, dataset and its
bytes, wall and policy seconds, network bytes (payload bytes per node from the stored
sizes, and each worker's interface counters) and the verification, with one digest of
every output's checksum.

### Computational storage against disaggregated, on the same data

The cephdag8 lab (`infra/labs/cephdag8.json`) runs either mode on one Ceph cluster:
`cephdag/clusters/cephdag8-cs.json` (8 nodes that store and compute) or
`cephdag8-disagg.json` (the same 8 pools without workers, then 8 compute-only workers).
Calibrate cs first; disagg takes the disks of its storage-only nodes from the cs
calibration (a node without a worker cannot time its own disk), and compute-only nodes
get placeholder disk rates that no forecast uses:

```sh
docker exec cephdag8-n0 python3 -m experiments.calibrate --network-mbit 400 \
    --cluster cephdag/clusters/cephdag8-cs.json --droid droid-128-02047 \
    --output /results/calibration/cephdag8-cs
docker exec cephdag8-n0 python3 -m experiments.calibrate --network-mbit 400 \
    --cluster cephdag/clusters/cephdag8-disagg.json --droid droid-128-02047 \
    --storage-from /results/calibration/cephdag8-cs/calibration.json \
    --output /results/calibration/cephdag8-disagg
```

(`infra/lab.py ... mode cs` or `mode disagg` before each, with `--functions cephdag.droid`.)
Native policies (beam, MCTS) need every node to store and compute, so they run in cs mode
only. `experiments/campaign.py` then drives a whole campaign from the host, outside the
containers: `plan SPEC` prints its steps, an hour estimate from the measured rates in the
spec and any missing input; `run SPEC` runs it, resumably and fail-closed (see its
docstring). `experiments/campaigns/` holds the demo-3 specs.

## Tests

The tests need Python 3.9 and NumPy 2.0.2, as in the lab, and the Rust library:

```sh
uv python install 3.9
uv venv -p 3.9 .venv
uv pip install -p .venv numpy==2.0.2
cargo build --release --offline --manifest-path native/Cargo.toml
.venv/bin/python -m unittest discover -s tests -t .
```

Storage and workers run in-process (`storage.MemoryStore`, `tests/helpers.py`), so no
Ceph is needed. `tests/test_equivalence.py` replays a fixture recorded from the original
code by `tests/fixtures/make_equivalence_fixture.py`: 3,000 policy decisions on 300 random
states, 400 complete simulated runs, 717 forecasts and 49 live-state reconstructions, all
of which must match. `tests/test_wire.py` compares the coordinator's requests with ones
recorded from the original coordinator and workers.

## Layout

```
cephdag/        cluster.py, graph.py, functions.py, storage.py, model.py,
                coordinator.py, worker.py, client.py, droid.py, policies/
native/         Rust beam search and MCTS
experiments/    calibrate.py, run.py, summarize.py, oracle.py, datasets.py,
                droid_ingest.py, droid_workload.py, droid_profile.py,
                campaign.py (host side, stdlib only) and campaigns/
infra/          lab creation and checks (Docker, Ceph, network shaping)
examples/       quickstart.py, custom_function.py
tests/
```

`CLEANUP.md` maps every file of the original lab to where it went.
