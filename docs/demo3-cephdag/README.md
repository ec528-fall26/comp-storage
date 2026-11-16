# Demo 3 results: the cephdag8 lab and its schedulers

Results, figures and raw run rows of the eight-storage-node computational-storage lab
(cephdag8) and the schedulers compared on it. The code is in [`../../cephdag/`](../../cephdag/README.md).
The initial submission was prepared on 2026-11-15 from the analysis VM (JarvisVM,
`~/projects/comp-storage/demo3/`). The complete work bundle was integrated on 2026-11-16,
including the analysis scripts, per-task records, slide sources, and source history.

Aakash's [current deck](../../slides/demo-3-aakash.pptx) has a
[ten-minute narrative](../demo3-narrative.md) covering its eighteen main slides.

## Start here

- To reproduce the tables and charts, use [Regenerating the tables and charts](#regenerating-the-tables-and-charts).
- To collect fresh measurements, follow the [lab setup](../design-document-multinode.md#3-setup) and [campaign instructions](../design-document-multinode.md#4-experiments-and-results).
- To inspect the implementation, start with the [code README](../../cephdag/README.md).

## Reading order

Start with the design document,
[`../design-document-multinode.md`](../design-document-multinode.md). The files below are the
detail behind it.

1. [`demo-data/SETUP.md`](demo-data/SETUP.md): how the system and the lab are built. The question
   and why the baseline is fair, the lab with its exact limits and rates, the client, coordinator
   and worker interfaces, the `choose(state)` policy contract, the workloads, the method, what was
   reused and what is custom, and the limitations. Diagrams: `demo-data/arch_lab.png` (the lab in
   both modes) and `demo-data/arch_software.png` (the software and one task's path).
2. [`demo-data/DEMO-DATA.md`](demo-data/DEMO-DATA.md): the data pack. Per story point, the
   headline numbers, the table, the caveat to say aloud and the source. Charts are
   `demo-data/fig*.png`; tables are `demo-data/tables.md` and one `demo-data/t*.csv` per table;
   `demo-data/checks.csv` counts rows, failed verifications and output digests per campaign.
3. The RESULTS files, one per campaign or stage, roughly in the order they ran (13-15 Nov 2026):

   | File | Campaign(s) | What it measures |
   |---|---|---|
   | [`RESULTS-sweep.md`](RESULTS-sweep.md) | demo3-sweep | 16 GiB round-robin, intensity sweep, computational storage (cs) against the disaggregated baseline (disagg) |
   | [`RESULTS-skew.md`](RESULTS-skew.md) | demo3-skew | 8 GiB with 65 of 128 shards on node 0 |
   | [`RESULTS-droid.md`](RESULTS-droid.md) | demo3-droid | DROID shards 0-549 (496 GB) in 7 batches |
   | [`RESULTS-sensitivity.md`](RESULTS-sensitivity.md) | demo3-sensitivity-sweep, -droid | two workers per compute node (disagg2) and fused DROID |
   | [`RESULTS-schedulers.md`](RESULTS-schedulers.md) | sched-pilot, sched-skew, sched-uniform | heft-cc, backlog and lp-plan against the existing policies |
   | [`RESULTS-overlap.md`](RESULTS-overlap.md) | demo3-overlap-sweep | disagg and disagg2 in one session |
   | [`RESULTS-scale.md`](RESULTS-scale.md) | demo3-scale | 256 GiB |
   | [`RESULTS-slots.md`](RESULTS-slots.md) | demo3-slots, demo3-slots-droid | slot-matched: cs2 against disagg2, 16 workers each |
   | [`RESULTS-lp-plan2.md`](RESULTS-lp-plan2.md) | sched-lp, sched-lp-low, sched-lp-uniform | lp-plan2 (lp-plan with a new dispatcher) |
   | [`RESULTS-mcts.md`](RESULTS-mcts.md) | sched-mcts | native MCTS on the lab |
   | [`RESULTS-sched-droid.md`](RESULTS-sched-droid.md) | sched-droid | schedulers on DROID with a hot node |

   The network-speed sweep (netsweep-400, -1000, -2500) has no RESULTS file; it is section 6 of
   DEMO-DATA.md.
4. Analyses behind the results (no runs of their own):
   - [`LAB-VALIDATION.md`](LAB-VALIDATION.md): the cephdag8 lab checked on 2026-11-12 (placement,
     routes, shaped bandwidth).
   - [`FAIRNESS.md`](FAIRNESS.md): is the disaggregated baseline fair? Analysis of the pilots.
   - [`SCHEDULERS.md`](SCHEDULERS.md): design of heft-cc, backlog and lp-plan, with in-process
     forecasts made before they ran.
   - [`DIAGNOSIS.md`](DIAGNOSIS.md): DROID data-local drift, lp-plan at i128/i1024, greedy-wait's
     planning time.
   - [`GUMBEL.md`](GUMBEL.md): Gumbel MCTS, built and evaluated in simulation only (a negative
     result). Its code is on the cephdag repository's branch `gumbel`, which never ran on the lab
     and is preserved in `history/cephdag-all-branches.bundle`.

## Other files

- `rows/<campaign>/<step>/a<attempt>/rows.jsonl`: one JSON line per run (mode, policy, wall time,
  bytes moved, verification, output digest) for the 17 campaigns DEMO-DATA.md uses, 190 files.
  Copied from `~/datasets/cephdag-demo3/campaigns/` on JarvisVM, sha256 matched.
- `sched-forecast/forecast.json`, `sched-forecast/lp_bound.jsonl` and `gumbel-eval/evaluation.jsonl`:
  the in-process forecasts and LP bounds that `make_figures.py` reads for DEMO-DATA.md sections 4
  and 5.
- `demo-data/make_figures.py` and `demo-data/make_diagrams.py`: regenerate the tables, charts and
  diagrams (below).
- `demo-data/t8_task_stages.csv`: median read, compute and write seconds per task in the
  slot-matched sweep at 1 and 256 iterations, from the per-run records.
- `droid_download.py`: the script that fetched the DROID shards from the public bucket and
  checked each against its MD5.

## Paths and commits in the documents

The documents were written on the analysis VM about a lab on another machine, so their paths name
one of two hosts:

- `/mnt/mass/...`, `~/runs/...` and `~/experiments/...` are on **aakashpc**, the lab host.
- `~/projects/comp-storage/...`, `~/datasets/...` and `~/.hermes/...` are on **JarvisVM**, the
  analysis VM. `~/projects/comp-storage/demo3/` is where these documents came from, and a
  document path without a directory (SETUP.md says so) is a file here: in `demo-data/` for the
  data pack, in this directory for the rest. PLAN.md, PILOTS.md, SENSITIVITY.md and the analysis
  scripts the documents cite (`sched_forecast.py`, `fairness_analysis.py`, `analysis/*.py`) are
  now included here. Historical run scripts retain their original host paths.

Commit hashes (`2b22dd6`, `cbd1561`, `bb7de4b`, `3bc0c28`, `ac2f85d`, `8cdc2b4`, `d7d3905`,
`29f8b37`, ...) are commits of the cephdag repository (JarvisVM, `~/projects/comp-storage/cephdag`).
All of them are ancestors of its branch `demo-3-final`, whose tree is `../../cephdag/`. That tree
is `8cdc2b4`, the code deployed for the last lab runs and the commit SETUP.md cites by file:line,
plus six campaign specs (`experiments/campaigns/sched-droid.json`, `sched-lp-low.json`,
`sched-lp-uniform.json`, `netsweep-{400,1000,2500}.json`), so those citations hold for
`../../cephdag/` too.

## Raw data

The complete supplied experiment records are in [`records/`](records), including per-task
timings, configs, ledgers, calibration, derived models, and deployment records. These 1,668
files occupy about 757 MB before Git compression. `records/campaigns/` contains all 26
campaigns. The DROID source (554 shards, 501.5 GB) remains on aakashpc at
`/mnt/mass/cephdag/droid/raw`; `droid_download.py` downloads it from the public release.

The slide sources and original renders are in [`slides/`](slides). The literature reviews
are in [`literature/`](literature), and the original project context is in
[`project-context.md`](project-context.md). Both supplied Git history bundles are in
[`history/`](history), including the experimental Gumbel branch.

## Regenerating the tables and charts

From the repository root, install Matplotlib in a temporary environment and regenerate
the tables, plots, and architecture diagrams

```sh
python3 -m venv /tmp/figs-venv
/tmp/figs-venv/bin/python -m pip install matplotlib
/tmp/figs-venv/bin/python -I docs/demo3-cephdag/demo-data/make_figures.py docs/demo3-cephdag/records/campaigns /tmp/demo3-figs
/tmp/figs-venv/bin/python -I docs/demo3-cephdag/demo-data/make_diagrams.py /tmp/demo3-figs
```

The output directory contains `tables.md`, 21 CSV tables, 11 result plots, and two
architecture diagrams. `checks.csv` should report 422 rows across 17 campaigns, with
zero failed verifications and zero comparison cells containing multiple digests.
The regenerated CSV values match the committed tables. The archived plot environment
used Matplotlib 3.11.2, so fonts and rendering may vary across installations.

Without the output directory both scripts overwrite the files in `demo-data/`. The first argument
of `make_figures.py` is any directory laid out like `rows/`; a `netsweep-<Mbit>` campaign in it is
picked up automatically. As DEMO-DATA.md says, the "moved off node 0" columns and node 0's share
of the source (sections 4 and 7) come from the per-run records, so they are blank when the input
holds rows.jsonl only; pass `docs/demo3-cephdag/records/campaigns` to fill them. Charts use Calibri, then Carlito, then DejaVu Sans,
whichever is installed; the committed ones are DejaVu Sans.

## Rebuilding the slide deck

With Python, `python-pptx`, and its dependencies installed, run from the repository root

```sh
python3 docs/demo3-cephdag/slides/build_deck.py /tmp/demo-3-aakash.pptx
python3 -m unittest discover -s docs/demo3-cephdag/slides -p test_slides.py
```

The builder uses the supplied Demo 1 deck, `deck.json`, the measured CSV tables, and the
current `cephdag/examples/quickstart.py`. The committed presentation is the supplied
current deck. The revised ten-minute script is in `../demo3-narrative.md`.
