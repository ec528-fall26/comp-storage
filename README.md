# EC528 Project — Computational Storage

**Team:** Aakash, Harshada, Ivan, Jeehan, Mason
**Mentors:** Alex Merenstein, Vasily Tarasov, Anthony Hsu (IBM Research)
**Project:** A compute layer over Ceph that runs dataset preprocessing on the nodes that store the data, instead of shipping the data to separate compute nodes and back.

## What this is

Preparing robot-learning datasets such as DROID (8.7 TB raw, 1.7 TB in RLDS) means
reading terabytes, transforming them, and writing the results back; when storage and
compute are separate machines, most of those bytes cross the network twice. We run the
preprocessing next to the data on Ceph storage nodes, reusing the existing converters,
and measure how much network traffic that avoids and what it does to runtime.

Demo 2 (this branch): a single-node Ceph cluster with CephFS; DROID 1.0.1 RLDS shards
loaded into it; Hugging Face's own RLDS → LeRobot converter run unmodified, either on a
separate compute node (the baseline) or on the storage node; verified, byte-identical
outputs written back to CephFS; and a background service that converts newly loaded
data on its own. See [`docs/design-document.md`](docs/design-document.md).

**Demo 2 result** (two EC2 `m6i.2xlarge`; measured 2026-10-16 and reproduced
2026-10-18 in a different AWS account): converting one 649 MB DROID shard on a separate
compute node moved 797 MB between the hosts. The same job on the storage node moved
0.2 MB (99.97% less), took the same time (304 s) and produced a byte-identical dataset.
Slides: [`slides/demo-2.pdf`](slides/demo-2.pdf). Demo video: `slides/demo-2-video.mp4`.

## Demo 3: multiple storage nodes and scheduling

[`cephdag/`](cephdag/README.md) runs static task graphs on nodes that both store data in Ceph
RADOS and compute on it, and chooses where each task runs and where its output is stored. Its lab,
cephdag8, is one Ceph 20.2.4 cluster of Docker containers on one host: 8 storage nodes that also
compute, and 8 compute-only nodes as the disaggregated baseline, with every link shaped to
400 Mbit/s. It holds the schedulers compared in Demo 3 (data-local, HEFT, heft-cc, backlog,
lp-plan2, beam search, native MCTS and others) and the DROID pipeline as a workload. Results,
figures and the raw run rows behind them are in
[`docs/demo3-cephdag/`](docs/demo3-cephdag/README.md). Start with the design document,
[`docs/design-document-multinode.md`](docs/design-document-multinode.md).

Aakash's current slides are in [`slides/demo-3-aakash.pptx`](slides/demo-3-aakash.pptx),
with a [ten-minute slide-by-slide narrative](docs/demo3-narrative.md). The complete
experiment records, analysis scripts, slide sources, and source-history bundles are
included under [`docs/demo3-cephdag/`](docs/demo3-cephdag/README.md).

## Quick start

On any Linux machine with Docker, or a Mac with Docker Desktop; no cloud account
needed (synthetic data, a local directory standing in for CephFS):

```bash
git clone -b demo-2 https://github.com/ec528-fall26/comp-storage && cd comp-storage
experiments/00_smoke_local.sh     # ends with "SMOKE TEST PASSED"
```

The real testbed (two EC2 instances; about an hour and $1 including setup):

```bash
infra/aws/up.sh && infra/deploy.sh
# then, on the compute node:  cd comp-storage && experiments/run_all.sh
infra/aws/down.sh                 # when finished
```

## Repository layout

| Path | Contents |
| --- | --- |
| `docs/` | Design proposal and design document |
| `slides/` | Demo slides (`demo-1.pdf`, `demo-2.pdf`, ...) and demo videos |
| `bin/cstore` | Command-line entry point |
| `src/cstore/` | Jobs, worker, loader, queue and background service, benchmark driver, verification |
| `src/worker/` | Worker container image (pinned conversion stack) |
| `src/third_party/` | Hugging Face's `port_droid.py`, vendored unmodified |
| `infra/` | AWS provisioning, node setup (MicroCeph, CephFS, Docker), deployment |
| `experiments/` | Scripts that reproduce every result we claim |
| `tests/` | Unit tests: `python3 -m unittest discover -s tests` |

## Reproducing our results

See [`docs/design-document.md`](docs/design-document.md). Every claim we make in a
demo or in the final presentation has a corresponding script in `experiments/`.

## Submission checklist

Deliverables are collected from a **branch named for the demo**, at **12:00 noon**
on the day of that demo. See the
[submission instructions](https://ec528.github.io/ec528/fall26/setup/).

| Deliverable | Branch | Must contain |
| --- | --- | --- |
| Demo 1 | `demo-1` | slides, code, `docs/design-proposal.md` |
| Demo 2 | `demo-2` | slides, code, `docs/design-document.md`, demo video |
| Demo 3 | `demo-3` | slides, code, updated `docs/design-document.md`, demo video |
| Final | `final-demo` | slides, code, artifact documentation, recorded video presentation |
