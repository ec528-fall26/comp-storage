# Schedulers on the real DROID pipeline with a hot node (sched-droid, finished 2026-11-15 15:47 ET)

Question: on the DROID pipeline, where demo3-droid gave the scheduler nothing to decide (data spread
evenly), do the contention-aware schedulers that won on the synthetic skew graph (heft-cc, lp-plan2)
also win when one node holds most of the data?

Setup. Fused DROID (one `droid_fused@1` task per partition: decode, resize to 128x128, encode video,
frame statistics and state Parquet in memory), shards 500-515 re-ingested from
`/mnt/mass/cephdag/droid/raw` as two waves of 8 shards, each placed with the pattern
`0,0,0,0,0,0,0,0,0,1,2,3,4,5,6,7` (droid_ingest cycles it over each shard's partitions, offset by
the shard number), so node 0 holds 9 of every 16 partitions. cs mode: data-local, heft-cc,
lp-plan2, heft (8 workers, one per storage node); disagg mode: fifo-hash (8 compute-only workers),
the baseline. One run per configuration per wave; the two waves are the two repeats, on different
data. cephdag8, tbf 400 Mbit, deployed code mcts-lab `8cdc2b4` (unchanged since stage E), spec
`sched-droid.json` on cephdag branch `sched-gaps` (`d7d3905`), run from the deployed clone by runjob
`demo3-gaps` on aakashpc (started 15:10 ET). cs models `derived-models/cephdag8-cs-fused`
(sha256 `5d36f8c8…`), the file the fused sensitivity runs used.

All 10 runs verified (each read back its 4.8-4.9 GB of videos, statistics and Parquet in 87-96 s);
within each wave all five configurations produced one output digest.

| wave | shards | source GB | partitions (tasks) | on node 0 |
|---|---|---|---|---|
| 0 | 500-507 | 6.91 | 217 | 115 partitions, 54.4% of bytes |
| 1 | 508-515 | 6.75 | 224 | 136 partitions, 62.0% of bytes |

Wall seconds per wave, ratio to data-local in the same wave, and what the scheduler did:

| configuration | wave 0 s | wave 1 s | over data-local (w0, w1) | planning s (w0, w1) | wall minus planning s | GB moved (w0, w1) | tasks moved off node 0 (w0, w1) |
|---|---|---|---|---|---|---|---|
| cs data-local | **73.8** | **78.0** | 1, 1 | 0.03, 0.03 | 73.8, 78.0 | 3.26, 3.37 | 83, 100 |
| cs heft-cc | 85.6 | 87.6 | 1.160, 1.122 | 8.1, 7.9 | 77.5, 79.6 | 3.82, 4.29 | 79, 98 |
| cs lp-plan2 | 86.7 | 99.7 | 1.175, 1.278 | 17.4, 23.1 | 69.3, 76.6 | 2.50, 2.75 | 81, 96 |
| cs heft | 87.9 | 85.9 | 1.191, 1.101 | 1.2, 1.2 | 86.7, 84.6 | 5.15, 4.63 | 93, 106 |
| disagg fifo-hash | 117.6 | 116.1 | 1.594, 1.488 | 0.01, 0.01 | | 11.79, 11.53 | (all reads remote) |

Medians over the two waves (mean of two): data-local 75.9 s, heft-cc 86.6, heft 86.9, lp-plan2
93.2, disagg fifo-hash 116.8. "Planning" is the run's `policy_seconds`; almost all of it is the first
decision, before any task starts (lp-plan2's first task was submitted at 17.3 and 22.9 s, heft-cc's
at 8.1 and 7.9 s), so the whole cluster is idle for it.

Reading.

- data-local wins on both waves, by 12-16% over heft-cc and 17-28% over lp-plan2. Every cs policy
  beats the baseline: data-local by 1.59x and 1.49x, with 3.3 GB moved against 11.7 GB.
- The planners lose on planning time, not on their plans. Take the planning out and lp-plan2's plan
  is the fastest execution in both waves (69.3 s against data-local's 73.8 in wave 0, 76.6 against
  78.0 in wave 1) and moves the fewest bytes (2.5-2.75 GB); heft-cc's is 2-5% slower than
  data-local even then. But lp-plan2 spends 17-23 s solving its LP and heft-cc 8 s building its plan,
  while the whole job is 74-78 s.
- Why lp-plan2 plans so long here: it groups tasks into classes by function, parameters and input
  node, and every DROID partition has its own size and step count, so the LP has one class per task
  (217 and 224 classes, 8 workers each) where the synthetic skew graph had 8. The dense simplex then
  takes 17-23 s on the lab (51-69 s on JarvisVM, in-process). heft-cc's 8 s is its booked-link
  search over three outputs per task; plain HEFT plans in 1.2 s.
- The in-process fair-sharing forecast on the measured graphs (`sched-droid/measured.json`, built
  from each run's config.json, planning not charged) predicts the execution after planning within
  1-6%: data-local 71.7 and 73.5 s (measured 73.8, 78.0), heft-cc 76.3 and 78.6 (77.5, 79.6),
  lp-plan2 67.4 and 74.4 (69.3, 76.6), heft 85.7 and 81.4 (86.7, 84.6). So the plans behave as
  modelled; what the model does not charge is the planning.
- Unlike the synthetic skew graph, moving data off node 0 is cheap relative to compute here (a
  31 MB partition crosses the link in about 0.7 s and takes about 2 s to process), so a free worker
  pulling the next task, which is what data-local does once its own node is empty, already balances
  the load: all four cs policies move 79-106 of node 0's 115-136 partitions.

Paired within this campaign only (host storage drifts across days, DIAGNOSIS.md section 1). Two
waves of different data are two repeats, not replicates of one input; wave 1 is more skewed (62%
against 54% of bytes on node 0). lp-plan2's planning differed by 5.7 s between the waves (217 against
224 classes).

Before the run (plan, not result): an in-process check on a synthetic graph with partitions of
equal size per shard (`sched_droid_check.py`, `sched-droid/check.json`) completed all four policies,
gave lp-plan2 64 classes and a 0.23-0.36 s LP solve, and forecast lp-plan2 first (68.1 and 73.6 s). The
real partitions differ in size, which multiplied the classes by 3.4 and the solve time by about 200
(on JarvisVM); that check missed it.

Not covered: unfused DROID with skew (the frames intermediate, where placement of outputs matters
most); cs2 (two workers per node); more than two waves. Changing lp-plan2 to group partitions by size
(8 classes instead of 217) would remove most of its planning, but that is a policy change and was
not made.

Sources: aakashpc `/mnt/mass/cephdag/campaigns/sched-droid/` (durable copy, checksums matched,
`~/datasets/cephdag-demo3/campaigns/sched-droid/`); run log `~/runs/demo3-gaps-20261115-201003/` on
aakashpc; tables `demo-data/t7_sched_droid.csv`, `t7_sched_droid_waves.csv`; chart
`demo-data/fig7_sched_droid.png`; forecasts `sched-droid/check.json`, `sched-droid/measured.json`.
