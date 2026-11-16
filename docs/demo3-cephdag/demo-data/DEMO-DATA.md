# Demo 3 data pack

How the system and the lab are built (interfaces, design choices and why, limitations) is in
`SETUP.md`, with two slide-ready diagrams: `arch_lab.png` (the cephdag8 lab in cs and disagg
modes) and `arch_software.png` (client, coordinator, policy and planner, workers, RADOS, and
one task's path). `make_diagrams.py` redraws both.

Built 2026-11-14 from the raw run records (rows.jsonl) of the cephdag8 campaigns on aakashpc,
copied read-only to JarvisVM. Every number here is printed by `make_figures.py` into
`tables.md` and the CSVs, and was checked against the RESULTS file named in each section; where
the two disagree it says so ("Disagreements" at the end).

Regenerate everything (pass a fresh rows directory to pick up reruns):

```
mkdir ROWS_DIR && cd ROWS_DIR
ssh aakashpc "sh -c 'cd /mnt/mass/cephdag/campaigns && tar cf - CAMPAIGN/*/a*/rows.jsonl'" | tar xf -   # once per campaign
~/.hermes/cache/scratch/figs-venv/bin/python -I ~/projects/comp-storage/demo3/demo-data/make_figures.py ROWS_DIR
```

A durable copy of every campaign record (rows, per-run records, ledgers, calibration, deploy
records; 710 MB, 1,595 files, checksums matched against aakashpc on 2026-11-15) is in
`~/datasets/cephdag-demo3/` on JarvisVM; pass `~/datasets/cephdag-demo3/campaigns` as ROWS_DIR.
The originals stay on aakashpc's HDD under `/mnt/mass/cephdag/`. Any `netsweep-<Mbit>` directory in ROWS_DIR is picked up automatically.
The "moved off node 0" columns and node 0's share (sections 4 and 7) read the per-run records and
config.json beside rows.jsonl, so they are blank when ROWS_DIR holds rows.jsonl only; the durable
copy has them (sched-droid, sched-lp-low and sched-lp-uniform added 2026-11-15, checksums matched).

Conventions. Wall time is the median over repeats (with 2 repeats that is their mean). A speedup
is the baseline's best policy over computational storage's best policy within the same repeat,
then the median over repeats, as in the RESULTS files. "GB moved" is payload sent over the
network (median). Every run in every campaign passed output verification, and within each
intensity or batch all policies and both modes produced one output digest (`checks.csv`).

Lab: cephdag8 on aakashpc, 8 storage nodes (computational storage, "cs") and 8 compute-only
nodes (the disaggregated baseline, "disagg"), one Ceph cluster, every link shaped to 400 Mbit/s
(about 47 MB/s). "cs2"/"disagg2" run two workers per node, so both sides have 16 worker slots.

Charts are 1600x900 PNG, DejaVu Sans on this VM (the script picks Calibri, then Carlito, if
installed). Colours: blue = computational storage, orange = disaggregated baseline.

## 1. Computational storage against a fair baseline, across arithmetic intensity

Headline numbers:

- 5.6x faster at 1 kernel iteration, 1.08x at 4096, never below 1x (slot-matched: 16 workers
  against 16, 16 GiB, 256 tasks).
- The baseline moves 21.5 GB per run; computational storage moves at most 0.27 GB.
- Unmatched (8 cs workers against 16 baseline workers) cs fell to 0.72x at 4096 iterations:
  that crossover was the worker count, not the architecture.
- Single-worker sweep the day before: 6.43x at 1 iteration to 1.10x at 4096.

| iterations | cs2 data-local s | cs2 heft s | disagg2 s | slot-matched speedup | slot-matched, data-local only | single slot (Nov 13) | 8 cs vs 16 disagg2 workers (Nov 14) |
|---|---|---|---|---|---|---|---|
| 1 | 15.2 | 11.6 | 64.2 | 5.60 (4.82, 6.39) | 4.31 | 6.43 | 4.79 |
| 4 | | | | | | 6.41 | |
| 16 | | | | | | 5.86 | 4.54 |
| 64 | 16.2 | 12.9 | 65.8 | 5.13 (4.88, 5.37) | 4.12 | 4.03 | 3.31 |
| 256 | 31.2 | 31.1 | 71.8 | 2.36 (2.21, 2.52) | 2.32 | 2.20 | 1.55 |
| 1024 | 97.6 | 95.0 | 118.9 | 1.25 (1.29, 1.22) | 1.22 | 1.42 | 0.83 |
| 4096 | 364.8 | 362.6 | 392.4 | 1.08 (1 run) | 1.08 | 1.10 | 0.72 |

Say aloud: the slot-matched campaign has 2 repeats (1 at 4096), and at 1 iteration they gave
4.8x and 6.4x; with data-local alone (the RESULTS file's choice) it is 4.2-4.3x. "4-6x at low
intensity" is the defensible phrasing. The single-slot line is from the day before, when host
storage was faster (DIAGNOSIS.md section 1), so compare the two lines' shapes, not their values.

Charts: `fig1_speedup_vs_intensity.png` (slot-matched and single-slot, 1x line),
`fig1b_slot_matching.png` (why slot matching matters: same day, 8 vs 16 workers against 16 vs 16).
Tables: `t1_slots.csv`, `t1_sweep.csv`, `t1_overlap.csv`.
Sources: campaigns demo3-slots, demo3-sweep, demo3-overlap-sweep; RESULTS-slots.md,
RESULTS-sweep.md, RESULTS-overlap.md.

## 2. The real pipeline at scale: DROID, 496 GB

Headline numbers:

- 496 GB of DROID video, 64,144 tasks in 7 batches: 1.55 h on computational storage against
  5.85 h disaggregated, 3.76x.
- Data moved: 6.2 GB against 4,107 GB (4.1 TB, 8.3x the source, because the decoded frames are
  written once and read twice across the network).
- Node-hours: 8 nodes x 1.55 h = 12.4 against 16 nodes x 5.85 h = 93.6, 7.5x, assuming a storage
  node and a compute node cost the same.
- Honest caveat in numbers: with the pipeline fused (frames kept in memory), the slot-matched
  advantage on a 3-shard sample is 1.79x, against 4.30x unfused.

| batch | source GB | tasks | cs data-local s | cs beam-4 s (planning) | disagg heft s | speedup | GB moved, data-local / beam-4 / heft |
|---|---|---|---|---|---|---|---|
| 0 | 74.8 | 9,704 | 767.7 | 952.0 (81.9) | 3,203.1 | 4.17 | 1.07 / 3.31 / 620.6 |
| 1 | 70.3 | 9,132 | 756.8 | 873.6 (75.0) | 2,971.6 | 3.93 | 0.89 / 2.95 / 583.9 |
| 2 | 69.8 | 9,020 | 787.8 | 860.8 (80.1) | 2,947.1 | 3.74 | 1.10 / 3.43 / 577.2 |
| 3 | 73.4 | 9,484 | 844.2 | 928.4 (83.7) | 3,099.8 | 3.67 | 1.00 / 3.35 / 608.4 |
| 4 | 71.7 | 9,196 | 829.1 | 906.3 (80.4) | 3,059.2 | 3.69 | 0.72 / 3.47 / 591.8 |
| 5 | 71.3 | 9,244 | 837.5 | 923.5 (82.9) | 3,026.5 | 3.61 | 0.60 / 3.21 / 589.1 |
| 6 | 64.9 | 8,364 | 772.2 | 847.4 (67.0) | 2,751.6 | 3.56 | 0.82 / 3.44 / 536.0 |
| all | 496.2 | 64,144 | 5,595 (1.55 h) | 6,292 (551) | 21,059 (5.85 h) | 3.76 | 6.2 / 23.2 / 4,107 |

Fused against unfused, DROID shards 550-552 (2.7 GB), one run each:

| pipeline | tasks | cs2 s | disagg2 s | disagg2 / cs2 | GB moved, cs2 / disagg2 | stage A: disagg heft / cs, disagg2 / cs (1-slot cs) |
|---|---|---|---|---|---|---|
| unfused | 352 | 21.2 | 91.0 | 4.30 | 0.69 / 22.28 | 3.64, 2.97 |
| fused | 88 | 13.1 | 23.5 | 1.79 | 0.08 / 4.67 | 1.63, 0.99 |

Say aloud: most of the 3.8x comes from storing the frames intermediate. Fuse the pipeline and the
slot-matched advantage is 1.8x (one run, 3 shards); against an unmatched 16-worker baseline it was
a tie. Second caveat: the speedup slid from 4.17x to 3.56x across batches because the host's
storage got slower on every node during the run (DIAGNOSIS.md section 1), which slows computational
storage but not the network-bound baseline; at batch 0's rate (10.26 s per source GB) the total
would be 4.14x (estimate). Each batch is one run per configuration. Beam-4 is 12% slower than
data-local overall, most of it its 551 s of planning.

Charts: `fig2_droid_batches.png` (wall per batch, speedup on top), `fig2b_droid_fused.png`.
Tables: `t2_droid_batches.csv`, `t2_droid_totals.csv`, `t2_droid_fused.csv`.
Sources: campaigns demo3-droid (Nov 13-14), demo3-slots-droid and demo3-sensitivity-droid (Nov 14);
RESULTS-droid.md, RESULTS-slots.md, RESULTS-sensitivity.md, DIAGNOSIS.md section 1.

## 3. Scale: 256 GiB synthetic

Headline numbers:

- 256 GiB (274.9 GB), 4,096 tasks: 5.0x at 1 iteration (227.8 s against 1,139.3 s), 2.2x at 256
  (736.9 s against 1,631.9 s).
- The baseline moves 343.6 GB per run; computational storage with data-local moves 0.9-2.9 GB.
- Plain HEFT on computational storage moved 157 GB at 256 iterations and ran 59% behind
  data-local.

| iterations | cs data-local s | cs heft s | disagg s | speedup, best cs | speedup, data-local | GB moved, data-local / heft / disagg |
|---|---|---|---|---|---|---|
| 1 | 233.8 | 227.8 | 1,139.3 | 5.00 | 4.87 | 2.9 / 4.9 / 343.6 |
| 256 | 736.9 | 1,175.3 | 1,631.9 | 2.21 | 2.21 | 0.9 / 157.2 / 343.6 |

Say aloud: one run per cell, one worker per node on both sides (not slot-matched). At 1
iteration the speedup is lower than at 16 GiB (6.43x) because cs time grew 19-21x for 16x the
data; cache effects at 16 GiB and the slower host storage are not separated.

Chart: `fig3_scale_256gib.png` (bar labels are GB moved). Table: `t3_scale.csv`.
Source: campaign demo3-scale (Nov 14); RESULTS-scale.md.

## 4. Scheduling with uneven data

Headline numbers:

- One change, booking link time, takes HEFT from last to first on skewed data: 45.5 s to 18.3 s
  at 1 iteration (2.5x), and heft-cc wins at 16, 128 and 1024 iterations.
- heft-cc is the only policy within 5% of the best at every intensity; with even data it moves
  0 GB and ties data-local, where plain HEFT moves 5-8 GB and loses 17-38%.
- The LP plan lost because of its dispatcher, as diagnosed and predicted before the run: the fixed
  dispatcher (lp-plan2) cuts 41.8 to 33.4 s at 128 iterations and 114.9 to 88.6 s at 1024 (forecast
  39.2 and 91.6 s), beating heft-cc by 6.4% and 6.7% while moving 56% and 33% fewer bytes.

Skewed placement (8 GiB, 65 of 128 shards on node 0), 3 repeats, median [min-max], GB moved;
best per column in bold:

| policy | i1 | i16 | i128 | i1024 |
|---|---|---|---|---|
| heft-cc | 18.3 [18.2-18.9], 2.35 GB | **20.2 [19.5-20.7], 2.62 GB** | **35.1 [35.1-35.2], 3.02 GB** | **95.4 [95.3-96.2], 4.50 GB** |
| backlog | **17.6 [17.2-19.9], 0.67 GB** | 22.2 [19.4-22.4], 0.87 GB | 38.8 [36.4-39.1], 1.34 GB | 108.3 [108.0-108.7], 2.68 GB |
| lp-plan | 20.2 [20.0-21.0], 0.81 GB | 24.0 [24.0-24.1], 0.94 GB | 43.3 [43.3-43.6], 1.61 GB | 114.9 [112.8-115.7], 3.22 GB |
| greedy-wait | 20.6 [20.2-21.9], 0 GB | 27.5 [25.3-28.3], 0 GB | 54.6 [53.7-55.2], 0 GB | 103.3 [102.9-105.3], 3.83 GB |
| stealing | 21.9 [21.8-23.1], 0.87 GB | 23.4 [22.9-24.6], 0.94 GB | 40.1 [38.5-40.6], 1.48 GB | 107.8 [104.0-108.7], 2.75 GB |
| data-local | 23.2 [22.6-24.2], 1.07 GB | 22.5 [22.2-24.8], 1.34 GB | 39.3 [37.8-39.5], 1.68 GB | 100.1 [97.8-104.2], 2.89 GB |
| beam-4 | 27.5 [26.6-30.9], 1.74 GB | 34.8 [32.0-35.9], 1.95 GB | 37.7 [37.5-38.3], 1.61 GB | 102.1 [101.6-102.2], 3.02 GB |
| heft | 45.5 [44.5-45.7], 2.68 GB | 39.2 [39.0-39.3], 3.83 GB | 56.4 [55.9-56.6], 3.89 GB | 108.1 [107.7-109.1], 4.43 GB |

Even placement (16 GiB round-robin), 2 repeats:

| policy | i256 | i1024 |
|---|---|---|
| heft-cc | **43.7, 0 GB** | **141.6, 0 GB** |
| data-local | 43.9, 0.07 GB | 141.8, 0 GB |
| backlog | 45.5, 0 GB | 142.8, 0 GB |
| lp-plan | 46.5, 0.07 GB | 142.0, 0 GB |
| heft | 60.2, 5.17 GB | 165.5, 8.05 GB |

The dispatcher fix, skewed placement, 3 repeats, another session the same day:

| iterations | policy | median s (repeats) | GB moved | forecast s | LP bound (model) s |
|---|---|---|---|---|---|
| 128 | lp-plan | 41.8 (41.8, 43.8, 41.3) | 1.61 | | 34.6 |
| 128 | lp-plan2 | 33.4 (33.1, 33.4, 33.7) | 1.34 | 39.2 | 34.6 |
| 128 | heft-cc | 35.7 (35.3, 36.5, 35.7) | 3.02 | | 34.6 |
| 1024 | lp-plan | 114.9 (114.9, 118.0, 107.3) | 3.22 | | 78.1 |
| 1024 | lp-plan2 | 88.6 (90.0, 88.6, 88.0) | 3.02 | 91.6 | 78.1 |
| 1024 | heft-cc | 95.0 (94.9, 95.0, 95.2) | 4.50 | | 78.1 |

The skewed and even tables above now carry a row "lp-plan2 (other session)": its 128 and 1024
cells are from sched-lp (Nov 14), its 1 and 16 cells and the even-data cell from sched-lp-low and
sched-lp-uniform (Nov 15, section 7). Bold marks the best of sched-skew's own policies only;
compare lp-plan2 with the others in the paired tables of section 7.

Say aloud: the "LP bound" is a bound under the calibrated model, not on the lab: lp-plan2 ran
33.4 s at 128 iterations, under the model's 34.6 s, so the model is pessimistic there (its forecast was 39.2 s).
Compare policies within a table: the sched-skew baselines ran 4-7% slower than the same cells the
day before (storage drift).

Charts: `fig4_skew_policies.png` (wall over the best policy, per intensity), `fig4b_lp_plan2.png`.
Tables: `t4_sched_skew.csv` (and `_cells.csv`), `t4_sched_uniform.csv`, `t4_sched_lp.csv`.
Sources: campaigns sched-skew, sched-uniform, sched-lp (Nov 14); RESULTS-schedulers.md,
RESULTS-lp-plan2.md, DIAGNOSIS.md section 2, sched-forecast/lp_bound.jsonl.

## 5. Search does not beat a contention-aware heuristic, even with a perfect model

Headline numbers:

- On the lab, native MCTS-128 is 49% and 44% slower than heft-cc at 1 and 128 iterations (26.4
  against 17.7 s, 53.5 against 37.1 s) and moves the most data (3.5-5.9 GB); at 1024 it is 3.6%
  ahead (95.4 against 99.0 s), which is the size of heft-cc's session-to-session drift.
- In simulation, where the planner's model is exactly the world, no search beats heft-cc by more
  than 0.3% in any case; Gumbel MCTS-32 loses by 4-20% and averages 1.11x heft-cc's wall, no better
  than random restarts (1.10x).
- The simulator is good enough to screen planners before lab time: across 47 policy and intensity
  cells its forecast is within 2.5% of the lab median (median absolute error), 42 of 47 within 10%.

Lab, skewed placement, 2 repeats:

| iterations | policy | median s (repeats) | GB moved | planning s | forecast s |
|---|---|---|---|---|---|
| 1 | heft-cc | 17.7 (17.6, 17.8) | 2.35 | 0.05 | 20.2 |
| 1 | data-local | 21.9 (21.9, 22.0) | 1.04 | 0.02 | 22.7 |
| 1 | native-mcts-heuristic-128 | 26.4 (25.8, 27.1) | 3.48 | 0.52 | 26.6 |
| 1 | beam-4 | 36.0 (36.3, 35.7) | 2.28 | 0.37 | 31.3 |
| 128 | heft-cc | 37.1 (36.8, 37.5) | 3.02 | 0.05 | 37.7 |
| 128 | data-local | 37.3 (36.2, 38.4) | 1.58 | 0.02 | 40.4 |
| 128 | beam-4 | 37.3 (36.4, 38.2) | 1.64 | 0.49 | 39.2 |
| 128 | native-mcts-heuristic-128 | 53.5 (53.8, 53.3) | 4.30 | 0.77 | 49.5 |
| 1024 | native-mcts-heuristic-128 | 95.4 (96.6, 94.1) | 5.91 | 0.51 | 95.5 |
| 1024 | heft-cc | 99.0 (99.8, 98.3) | 4.50 | 0.06 | 93.8 |
| 1024 | beam-4 | 102.3 (101.5, 103.1) | 3.02 | 0.50 | 103.7 |
| 1024 | data-local | 103.9 (103.7, 104.1) | 2.82 | 0.02 | 101.8 |

Simulation, wall seconds with planning charged, mean of 3 seeds for searches:

| policy | skew-i1 | skew-i16 | skew-i128 | skew-i1024 | uniform-i256 | uniform-i1024 | mean / heft-cc |
|---|---|---|---|---|---|---|---|
| heft-cc | 20.3 | 22.9 | 37.8 | 93.9 | 43.8 | 143.2 | 1.00 |
| data-local | 22.7 | 23.7 | 40.4 | 101.8 | 44.1 | 143.0 | 1.05 |
| beam-4 | 31.3 | 32.6 | 39.3 | 102.6 | 43.8 | 143.1 | 1.18 |
| native-mcts-heuristic-128 | 24.9 | 30.0 | 50.3 | 95.5 | 53.2 | 154.3 | 1.20 |
| native-mcts-fair-128 | 24.7 | 26.2 | 39.3 | 94.4 | 54.0 | 154.5 | 1.12 |
| gumbel-lb-32 | 24.3 | 25.8 | 40.5 | 97.7 | 48.5 | 158.4 | 1.11 |
| restarts-32 | 23.2 | 26.2 | 43.7 | 93.6 | 47.9 | 153.7 | 1.10 |

Forecast against lab: forecast errors run from -9.8% to +17.3% ((forecast - measured) / measured);
the five cells outside 10% are lp-plan2 at i128 (+17.3%), backlog at i1 (+14.8%), beam-4 at i1
(+13.5%), heft-cc at i16 (+12.8%) and i1 (+10.1%), all forecasts that were too pessimistic.
Full list: `t5_forecast_vs_measured.csv`.

Say aloud: the simulation result is an upper bound on what search can gain, because the world is
the planner's model; the lab MCTS lead at 1024 iterations is within drift and rests on 2 repeats;
Gumbel MCTS has not been run on the lab. The MCTS forecasts are the planning-free seed-1 runs
(26.6/49.5/95.5 s), the ones quoted before the lab run.

Charts: `fig5_forecast_vs_measured.png` (y = x and ±10% lines), `fig5b_search_in_simulation.png`.
Tables: `t5_sched_mcts.csv`, `t5_search_simulated.csv`, `t5_forecast_vs_measured.csv`.
Sources: campaigns sched-mcts, sched-skew, sched-uniform, sched-lp; demo3/sched-forecast/forecast.json
(fair sharing), demo3/gumbel-eval/evaluation.jsonl; lp-plan2 forecast from DIAGNOSIS.md section 2
and PLAN.md; RESULTS-mcts.md, GUMBEL.md sections 6-7.

## 6. Network speed

Complete: runjob demo3-netsweep finished exit 0 at 19:27 ET; all 24 runs (3 rates x 2 intensities
x 2 repeats x 2 modes) verified, one output digest per intensity. No RESULTS file exists for it
yet, so these numbers are checked only against the prediction in PLAN.md.

Headline numbers:

- At 1 iteration the speedup tracks the link: 5.7x at 400 Mbit/s, 2.4x at 1000, 1.3x at 2500
  (predicted before the run: about 5.6x, 2.5x, 1.4x).
- At 256 iterations, where compute dominates, it changes less: 2.2x, 1.4x, 1.1x.
- Computational storage stays ahead at every rate tested, and moves 0 GB against 10.7 GB.

Slot-matched (cs2 heft against disagg2 fifo-hash, 16 workers each), 8 GiB of 64 MiB shards
round-robin, 128 tasks, 2 repeats, all rates in one session (1000, then 2500, then 400):

| link Mbit/s | iterations | cs2 s | disagg2 s | speedup (repeats) | GB moved, cs2 / disagg2 |
|---|---|---|---|---|---|
| 400 | 1 | 5.7 | 32.1 | 5.67 (4.94, 6.40) | 0 / 10.74 |
| 1000 | 1 | 5.5 | 13.3 | 2.44 (2.12, 2.76) | 0 / 10.74 |
| 2500 | 1 | 6.0 | 7.5 | 1.27 (1.33, 1.21) | 0 / 10.74 |
| 400 | 256 | 17.0 | 36.9 | 2.19 (1.98, 2.40) | 0 / 10.74 |
| 1000 | 256 | 15.1 | 21.0 | 1.39 (1.43, 1.35) | 0 / 10.74 |
| 2500 | 256 | 16.3 | 18.5 | 1.14 (1.12, 1.15) | 0 / 10.74 |

Say aloud: all 16 nodes are containers on one host, so at 2500 Mbit/s the links are emulated on a
shared disk and memory bus; the router carried 2357 Mbit/s per node in the pre-run check. Two
repeats per cell, and at 1 iteration the two disagree by up to 30% at 400 Mbit/s. The cs2 walls
move by 9-13% between rates although cs2 sends nothing over the network, which is the run-to-run
noise floor here.

Chart: `fig6_network_speed.png` (measured lines and the pre-run prediction). Table: `t6_netsweep.csv`.
Sources: campaigns netsweep-400, netsweep-1000, netsweep-2500 (Nov 14, 19:00-19:27 ET); prediction
in PLAN.md "Network-speed sweep".

## 7. Schedulers on the real pipeline with a hot node, and lp-plan2 at every intensity

Complete: runjob demo3-gaps on aakashpc, 15:10-16:28 ET Nov 15, exit 0; 40 runs (sched-droid 10,
sched-lp-low 24, sched-lp-uniform 6), all verified, one output digest per wave or intensity.
Checked against RESULTS-sched-droid.md and RESULTS-lp-plan2.md.

Headline numbers:

- Real pipeline (fused DROID, node 0 holding 54-62% of the data): plain data-local is fastest,
  73.8 and 78.0 s; heft-cc is 12-16% slower and lp-plan2 17-28% slower, because they plan for 8 s
  and 17-23 s while every worker waits. Computational storage still beats the disaggregated
  baseline 1.59x and 1.49x, moving 3.3 GB against 11.7 GB.
- Take the planning out and lp-plan2's plan executes fastest on DROID as well (69.3 and 76.6 s
  against 73.8 and 78.0) and moves the fewest bytes (2.5-2.75 GB).
- Synthetic skew: lp-plan2 is now first at every intensity measured, against heft-cc in the same
  session: 12.8 s against 18.3 at 1 iteration (28% faster), 16.0 against 19.0 at 16 (16%),
  33.4 against 35.7 at 128 and 88.6 against 95.0 at 1024 (6% and 7%, section 4). On even data it
  ties (45.9 s against heft-cc's 45.0 and data-local's 44.2, inside the 7.6% repeat spread).

Fused DROID, shards 500-515 in two waves of 8 (one run per configuration per wave), cs 8 workers
against disagg 8 compute-only workers; "plan" is the policy's time before and between dispatches:

| configuration | wave 0 s | wave 1 s | over data-local | plan s | GB moved | moved off node 0 |
|---|---|---|---|---|---|---|
| cs data-local | **73.8** | **78.0** | 1, 1 | 0.03 | 3.26, 3.37 | 83, 100 |
| cs heft-cc | 85.6 | 87.6 | 1.16, 1.12 | 8.1, 7.9 | 3.82, 4.29 | 79, 98 |
| cs lp-plan2 | 86.7 | 99.7 | 1.18, 1.28 | 17.4, 23.1 | 2.50, 2.75 | 81, 96 |
| cs heft | 87.9 | 85.9 | 1.19, 1.10 | 1.2, 1.2 | 5.15, 4.63 | 93, 106 |
| disagg fifo-hash | 117.6 | 116.1 | 1.59, 1.49 | 0.01 | 11.79, 11.53 | |

Waves: shards 500-507, 6.91 GB, 217 partitions, 54% of bytes on node 0; shards 508-515, 6.75 GB,
224 partitions, 62% on node 0.

Synthetic skew (sched-skew's 8 GiB, 65 of 128 shards on node 0), 3 repeats, median [min-max]:

| iterations | lp-plan2 | heft-cc | backlog | data-local |
|---|---|---|---|---|
| 1 | **12.8** [12.7-13.8], 0.47 GB | 18.3 [17.7-19.0], 2.35 GB | 15.8 [15.7-17.3], 0.60 GB | 21.4 [18.7-22.2], 1.28 GB |
| 16 | **16.0** [15.9-16.4], 0.60 GB | 19.0 [18.9-19.0], 2.62 GB | 19.0 [18.9-20.3], 0.74 GB | 22.1 [21.2-22.9], 1.34 GB |

lp-plan2 over heft-cc in the same repeat: 0.75, 0.67, 0.72 at 1 iteration; 0.84, 0.86, 0.84 at 16.

Say aloud: no single scheduler wins everywhere. lp-plan2's plans executed fastest in every skewed
case measured, synthetic and DROID, but on real DROID every partition differs in size, so its LP has
one class per task (217-224 instead of 8) and solving it costs 17-23 s on jobs of 74-100 s; there
plain data-local wins,
because moving a DROID partition is cheap next to processing it. Grouping partitions by size would
shrink the LP, but that is a policy change not made or measured. The DROID comparison is two waves
of different data, one run each. Compare within each table: these ran on Nov 15, and the same
heft-cc, backlog and data-local cells ran 0-14% faster here than in sched-skew on Nov 14.

Chart: `fig7_sched_droid.png` (wall per wave and configuration; hatched: planning while workers
idle). Tables: `t7_sched_droid.csv`, `t7_sched_droid_waves.csv`, `t4_sched_lp_low.csv`,
`t4_sched_lp_uniform.csv`. Sources: campaigns sched-droid, sched-lp-low, sched-lp-uniform;
RESULTS-sched-droid.md, RESULTS-lp-plan2.md (section "Low intensity and uniform data").

## Disagreements between the rows and the RESULTS files

1. Slot-matched speedup (section 1). RESULTS-slots.md and the ledger quote 4.22x at 1 iteration
   (ratio of medians, cs2 data-local only); the 5.60x headline is the best cs2 policy (heft at 1-64
   iterations) paired within repeat, the rule RESULTS-sweep.md uses for 6.43x. Paired data-local
   alone gives 4.31x. The rows support all three; they differ by policy choice and by pairing, and
   the two repeats at 1 iteration differ by 30%.
2. Scale (section 3). RESULTS-scale.md and the ledger quote 4.87x at 1 iteration (data-local); the
   5.0x figure uses the best cs policy (heft, 227.8 s). Same rows.
3. Fused DROID (section 2). Unrounded walls give 4.30x unfused; RESULTS-slots.md says 4.29x
   (computed from walls rounded to 0.1 s). Immaterial.
4. Forecast accuracy (section 5). RESULTS-schedulers.md and the ledger say the fair-sharing
   forecast was within 10% for heft-cc and backlog everywhere. Against the sched-skew medians it
   was not: backlog at i1 forecast 20.2 s against 17.6 s measured (+14.8%), heft-cc at i16 22.8
   against 20.2 (+12.8%), heft-cc at i1 20.2 against 18.3 (+10.1%).
5. Section 4. The LP bound at i128 (34.6 s) is above lp-plan2's measured 33.4 s; the RESULTS files
   call it a bound without saying it is model-relative.
6. Section 4. RESULTS-lp-plan2.md says lp-plan2 moves "30-55% fewer bytes" than heft-cc; the rows
   give 55.6% (i128) and 32.8% (i1024). Minor.

Every other number checked (sweep, overlap, DROID per batch and totals, the 4.14x drift estimate,
scale walls and bytes, all 8x4 sched-skew cells, sched-uniform, sched-lp, sched-mcts, the
simulated search table) matches its RESULTS file to the printed precision.
