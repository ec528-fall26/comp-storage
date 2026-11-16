# Native MCTS on the lab (stage E, cephdag8, sched-mcts finished 2026-11-14 18:37 EST)

Question: does the existing native MCTS (native-mcts-heuristic-128, no new scheduling code) do
better on cephdag8 than it did on the old 3-node lab, where it lost to HEFT and beam? Forecast
before the run (`sched_forecast.py`, fair-sharing): MCTS behind heft-cc at every intensity and
moving the most data.

Setup: sched-skew's 8 GiB (65 of 128 64 MiB shards on node 0), independent workload, ratio 0.25,
i1, i128, i1024, 2 repeats, cs mode, cephdag `8cdc2b4` (branch `mcts-lab`) deployed with the
tree-hash check, models matched SHA256SUMS, `verify cs` passed. 24 rows, all verified, one
outputs digest per intensity across policies (fc8b29d1 at i1, 100450be at i128, c5b014b3 at
i1024).

| case | policy | wall s, median (repeats) | moved GB | plan s | forecast s |
|---|---|---|---|---|---|
| i1 | heft-cc | 17.7 (17.6, 17.8) | 2.35 | 0.05 | 20.2 |
| i1 | data-local | 21.9 (21.9, 22.0) | 1.04 | 0.02 | |
| i1 | native-mcts-heuristic-128 | 26.4 (25.8, 27.1) | 3.48 | 0.52 | 26.6 |
| i1 | beam-4 | 36.0 (36.3, 35.7) | 2.28 | 0.37 | 31.3 |
| i128 | heft-cc | 37.1 (36.8, 37.5) | 3.02 | 0.05 | 37.7 |
| i128 | data-local | 37.3 (36.2, 38.4) | 1.58 | 0.02 | |
| i128 | beam-4 | 37.3 (36.4, 38.2) | 1.64 | 0.49 | 39.2 |
| i128 | native-mcts-heuristic-128 | 53.5 (53.8, 53.3) | 4.30 | 0.77 | 49.5 |
| i1024 | native-mcts-heuristic-128 | 95.4 (96.6, 94.1) | 5.91 | 0.51 | 95.5 |
| i1024 | heft-cc | 99.0 (99.8, 98.3) | 4.50 | 0.06 | 93.8 |
| i1024 | beam-4 | 102.3 (101.5, 103.1) | 3.02 | 0.50 | 103.7 |
| i1024 | data-local | 103.9 (103.7, 104.1) | 2.82 | 0.02 | |

Result: as forecast at i1 and i128, MCTS is far behind heft-cc (49% and 44% slower) and moves
the most data. At i1024 it is 3.6% ahead of heft-cc (95.4 vs 99.0 s), but that gap is the size
of heft-cc's session-to-session drift: heft-cc ran 95.4 s in sched-skew and 95.0 s in stage D
on the same graph, 99.0 s here. MCTS's own walls match its forecast to within 1% at i1 and i1024
and 8% at i128. lp-plan2 (stage D, another session) holds the best i1024 so far at 88.6 s.

Not established: whether the i1024 ordering of MCTS and heft-cc survives a same-session rerun
with more repeats; why heft-cc was slower in this session (the DROID drift diagnosis found
slower local storage on all nodes, which may be the same effect).

Source: aakashpc /mnt/mass/cephdag/campaigns/sched-mcts/*/a1/rows.jsonl; run log
~/runs/demo3-stage-e-20261114-192936/output.log on aakashpc.
