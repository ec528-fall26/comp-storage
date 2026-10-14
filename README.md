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

## Quick start

On any Linux machine with Docker, or a Mac with Docker Desktop; no cloud account
needed (synthetic data, a local directory standing in for CephFS):

```bash
git clone -b demo-2 https://github.com/ec528-fall26/comp-storage && cd comp-storage
experiments/00_smoke_local.sh     # ends with "SMOKE TEST PASSED"
```

The real testbed (two EC2 instances, ~30 min of experiments):

```bash
infra/aws/up.sh && infra/deploy.sh
# then, on the compute node:  cd comp-storage && experiments/run_all.sh
infra/aws/down.sh                 # when finished
```

## Repository layout

| Path | Contents |
| --- | --- |
| `docs/` | Design proposal and design document |
| `slides/` | Demo slides (`demo-1.pdf`, `demo-2.pdf`, ...) |
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
