# Computational storage, Demo 3: everything in one place

Packaged 2026-11-15 from JarvisVM. Sizes are before compression.

## Where to start

- `project/demo3/design-document-multinode.md`: the design document for the multi-node half
  (why, architecture, decisions, setup commands, six experiments with results, limitations).
- `project/demo3/demo-data/DEMO-DATA.md`: every headline number with its table, caveat and source.
- `slides/`: the deck so far and the figures.

## Contents

| Folder | What it is |
| --- | --- |
| `code/cephdag/` | The lab code as submitted (cephdag branch `demo-3-final`, aff24a2): client, coordinator, worker, the scheduling policies, the Rust planner (`native/`), tests. |
| `code/cephdag/infra/` | The lab itself: `lab.py`, `labs/cephdag8.json`, the Dockerfile and the hash-locked requirements. |
| `code/cephdag/experiments/` | Calibration, DROID ingest, the campaign driver and every campaign spec (`campaigns/*.json`). |
| `code/cephdag-all-branches.bundle` | Every branch of the cephdag repository with its history, including `gumbel` (Gumbel MCTS, simulation only). `git clone cephdag-all-branches.bundle cephdag-history` |
| `code/team-repo-demo-3-with-design-doc.bundle` | The two commits prepared for the team repository's `demo-3` branch (not pushed). |
| `project/AGENTS.md` | The project file: question, decisions, and the results ledger. |
| `project/demo3/` | Results (`RESULTS-*.md`), analyses (`FAIRNESS`, `DIAGNOSIS`, `SCHEDULERS`, `GUMBEL`, `LAB-VALIDATION`, `PILOTS`), the plan, and the scripts that ran the lab stages (`demo3-stage-*.sh`, `netsweep.sh`, `gaps.sh`, `disk-guard.sh`, `deploy_code.py`, `droid_download.py`). |
| `project/demo3/demo-data/` | The tables (CSV), charts and diagrams (PNG), `SETUP.md`, and `make_figures.py`, which rebuilds them from the rows. |
| `project/literature/` | The two literature reviews and the headroom analysis. The extracted paper texts are left out. |
| `project/bin/runjob` | The launcher used for every detached run. |
| `data/cephdag-demo3/campaigns/` | Every campaign's records: `rows.jsonl` (one line per run), the per-run records with per-task timings, ledgers and configs. 26 campaigns. |
| `data/cephdag-demo3/calibration/`, `derived-models/`, `deploy/` | The measured cost models and the lab's build and deploy records. |
| `slides/` | `demo-3-aakash.pptx` (the deck so far), `figures.pptx` (candidate figures), `demo-1.pptx` (the Demo 1 deck it starts from), the scripts that build them, and PNG renders. |

## Not included

- The DROID source: 554 shards, 501.5 GB, on aakashpc at `/mnt/mass/cephdag/droid/raw`.
  `project/demo3/droid_download.py` fetches it again from the public bucket.
- The lab's containers and Ceph volumes (on aakashpc, to be removed after the demo).
- The rest of the team repository (Mason's and Ivan's code and slides), which is on GitHub.
