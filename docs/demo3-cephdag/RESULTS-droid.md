# demo3-droid results (cephdag8, finished 2026-11-14 03:27 EST)

DROID 1.0.1 shards 0-549 (496.2 GB of source), 128x128, decode_resize -> encode_video,
frame_stats, state_parquet, processed as 7 batches of about 70 GB because the 448 GiB cluster
cannot hold the source and its 2.16x frames at once. Each batch is one graph of 8,364-9,704
tasks, 64,144 tasks in all; every configuration ran on the same ingested batch before the next
was loaded. cephdag `2b22dd6`, tbf 400 Mbit, 1 run per configuration per batch. All 21 runs
verified; within each batch all three configurations produced one output digest. Raw rows:
aakashpc `/mnt/mass/cephdag/campaigns/demo3-droid/wave0*/a1/rows.jsonl`.

| batch | source GB | tasks | cs data-local | cs beam-4 (planning) | disagg heft | speedup | moved GB, data-local / beam-4 / heft |
|---|---|---|---|---|---|---|---|
| 0 | 74.8 | 9,704 | 768 s | 952 s (82 s) | 3,203 s | 4.17x | 1.1 / 3.3 / 621 |
| 1 | 70.3 | 9,132 | 757 s | 874 s (75 s) | 2,972 s | 3.93x | 0.9 / 3.0 / 584 |
| 2 | 69.8 | 9,020 | 788 s | 861 s (80 s) | 2,947 s | 3.74x | 1.1 / 3.4 / 577 |
| 3 | 73.4 | 9,484 | 844 s | 928 s (84 s) | 3,100 s | 3.67x | 1.0 / 3.4 / 608 |
| 4 | 71.7 | 9,196 | 829 s | 906 s (80 s) | 3,059 s | 3.69x | 0.7 / 3.5 / 592 |
| 5 | 71.3 | 9,244 | 837 s | 923 s (83 s) | 3,026 s | 3.61x | 0.6 / 3.2 / 589 |
| 6 | 64.9 | 8,364 | 772 s | 847 s (67 s) | 2,752 s | 3.56x | 0.8 / 3.4 / 536 |
| all | 496.2 | 64,144 | 5,595 s (1.55 h) | 6,292 s (551 s) | 21,059 s (5.85 h) | 3.76x | 6.2 / 23 / 4,107 |

Reading. Computational storage processed the 496 GB in 1.55 h against 5.85 h for the
disaggregated baseline, and moved 6.2 GB against 4.1 TB: the baseline moves 8.3x the source,
because the frames (2.16x the source) are written once and read twice across the network.
Beam-4 is 12% slower than data-local overall; its planning (551 s) accounts for most of that.

Not explained yet: data-local's time per source GB rose steadily from 10.26 s/GB (batch 0) to
11.90 s/GB (batch 6), +16%, while beam-4 rose only 3% (12.72 to 13.06) and HEFT stayed flat
(42.24-42.80). So the speedup slid from 4.17x to 3.56x. Because the other two configurations
ran on the same data in the same batches, the cause is probably specific to data-local's
dispatch (for example how unevenly each batch's partitions landed across the 8 pools), not
storage ageing. To check: per-node task counts and busy time per batch.

The fairness checks (RESULTS-sensitivity.md) show most of the 3.8x comes from persisting the
frames intermediate: with a fused pipeline the advantage on a 3-shard sample is 1.0-1.6x.
