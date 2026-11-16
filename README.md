# Computational Storage — EC528 Demo 3

**Team** — Aakash, Harshada, Ivan, Jeehan, Mason

**Mentors** — Alex Merenstein, Vasily Tarasov, Anthony Hsu, IBM Research

Preparing a robot-learning dataset means reading camera recordings and robot state,
transforming them, and writing the results back to storage. When workers run beside
the stored data, much of that traffic stays local. This project measures when that
saves time and how to schedule the work across multiple storage nodes.

## What is in this branch

Demo 3 contains two implementations with their own clients and testbeds.

| Component | What it does | Start here |
| --- | --- | --- |
| Keyword-slice API | Accepts keywords over HTTP and filters DROID episodes on a storage node. Uses the CephFS job queue and worker from Demo 2. | [API design and reproduction](docs/design-document.md) |
| Multi-node scheduler | Executes task graphs over Ceph RADOS, choosing where each task runs and where its outputs are stored. Evaluated on eight storage nodes emulated on one host. | [Multi-node design and reproduction](docs/design-document-multinode.md) |

The API currently submits to the CephFS worker. Connecting it to the multi-node graph
scheduler is future work.

## Start here

```bash
git clone --branch demo-3 https://github.com/ec528-fall26/comp-storage.git
cd comp-storage
```

All commands in this README start at the repository root.

| Goal | Instructions |
| --- | --- |
| Understand the presentation | [Aakash's slides](slides/demo-3-aakash.pptx), [ten-minute narrative](docs/demo3-narrative.md), and [team slides](slides/Demo3.pptx) |
| Recreate the published charts from the included measurements | Run the commands below. Requires Python and Matplotlib. |
| Run the multi-node code's local tests | Follow [local tests](docs/design-document-multinode.md#local-tests). Requires Python, NumPy, DROID dependencies, and Rust. |
| Rerun the multi-node experiments | Follow [lab setup](docs/design-document-multinode.md#3-setup), then [experiments](docs/design-document-multinode.md#4-experiments-and-results). Requires a Linux Docker host with sufficient CPU, memory, and disk. |
| Run the keyword-slice API or reproduce its AWS measurement | Follow [the API setup](docs/design-document.md#the-slice-api-demo-3) and [experiment 12](docs/design-document.md#experiment-12-in-place-vs-send-everything). |
| Check the original conversion pipeline locally | With Docker running, execute `bash experiments/00_smoke_local.sh`. Success ends with `SMOKE TEST PASSED`. |

### Recreate the published charts

The repository includes the experiment records used for the presentation. Recreating
their tables and charts takes those saved measurements as input.

```bash
python3 -m venv /tmp/demo3-figures-venv
/tmp/demo3-figures-venv/bin/python -m pip install matplotlib
/tmp/demo3-figures-venv/bin/python docs/demo3-cephdag/demo-data/make_figures.py \
    docs/demo3-cephdag/records/campaigns /tmp/demo3-figures
/tmp/demo3-figures-venv/bin/python docs/demo3-cephdag/demo-data/make_diagrams.py \
    /tmp/demo3-figures
```

Open `/tmp/demo3-figures/tables.md` and the generated PNGs. `checks.csv` reports
422 measured runs across 17 plotted campaigns, zero failed verifications, and one
output digest per comparison cell. Fonts and rendering can vary with the local
Matplotlib installation. The [results guide](docs/demo3-cephdag/README.md) maps the
tables and records to each experiment.

## Main results

| Experiment | Configuration | Measured result |
| --- | --- | --- |
| Keyword slice | Two AWS instances, six DROID shards | 33.4 s in place and 33.7 s on separate compute. Cross-host traffic fell from 6,127.5 MB to about 0.1 MB. |
| Compute intensity | Eight storage nodes, 16 workers per mode, 16 GiB of synthetic data | The plotted speedup fell from 5.60× to 1.08× as computation per byte increased. |
| DROID preprocessing | 496 GB in seven batches, eight workers per mode, stored intermediate frames | 5.85 h to 1.55 h, a 3.76× speedup. Network traffic fell from 4,107 GB to 6.2 GB. |
| Uneven data | 65 of 128 synthetic shards on one node | Link-aware HEFT cut the light-workload run from 45.5 s to 18.3 s. A one-time linear program with an improved dispatcher reduced it further in a subsequent campaign. |

The multi-node measurements use real Ceph in containers sharing one physical host,
with shaped network links. The design document records the policies, worker counts,
repeat counts, and limitations behind each comparison.

## Repository layout

| Path | Contents |
| --- | --- |
| `bin/`, `src/cstore/` | Keyword-slice client and API, CephFS queue, service, and worker |
| `src/worker/`, `src/third_party/` | Container environment and the LeRobot converter |
| `infra/`, `experiments/`, `tests/` | API/conversion deployment, experiments, and tests |
| `cephdag/` | Multi-node client, scheduler, workers, Rust planner, lab, campaigns, and tests |
| `docs/design-document.md` | API/conversion design and reproduction |
| `docs/design-document-multinode.md` | Multi-node design and reproduction |
| `docs/demo3-cephdag/` | Results, analyses, charts, slide sources, and source-history bundles |
| `docs/demo3-cephdag/records/` | Complete supplied experiment records, about 757 MB before Git compression |
| `slides/` | Current presentation files |

The raw DROID corpus is downloaded during lab setup. The included `records/` directory
contains measurements and execution traces. Earlier submissions are retained on the
`demo-1` and `demo-2` branches. The original scope is in the
[design proposal](docs/design-proposal.md).
