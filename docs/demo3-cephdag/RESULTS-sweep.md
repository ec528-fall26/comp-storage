# demo3-sweep results (cephdag8, finished 2026-11-13 08:43 EST)

16 GiB of 64 MiB shards, round-robin over the 8 storage nodes, independent workload, ratio
0.25, chunked numeric kernel, tbf 400 Mbit per link, 3 repeats, cephdag `2b22dd6`. 84 runs,
every one verified, one output digest per intensity across all policies and both modes.
Raw rows: aakashpc `/mnt/mass/cephdag/campaigns/demo3-sweep/*/a1/rows.jsonl`.

Median wall seconds over the 3 repeats; speedup is the best disaggregated policy's wall over
the best computational-storage policy's wall within the same repeat.

| iterations | cs heft | cs data-local | disagg fifo-hash | disagg heft | speedup per repeat | median speedup | payload moved GB, cs / disagg |
|---|---|---|---|---|---|---|---|
| 1 | 11.1 | 12.1 | 72.3 | 71.4 | 6.46, 6.42, 6.43 | 6.43 | 0.20 / 21.47 |
| 4 | 11.3 | 13.4 | 73.1 | 73.7 | 6.41, 6.47, 6.35 | 6.41 | 0.20 / 21.47 |
| 16 | 13.2 | 13.7 | 74.4 | 73.4 | 5.86, 4.82, 5.90 | 5.86 | 0.20 / 21.47 |
| 64 | 20.7 | 19.9 | 80.2 | 79.5 | 4.56, 3.59, 4.03 | 4.03 | 0.47 / 21.47 |
| 256 | 60.3 | 47.9 | 105.1 | 105.7 | 2.45, 2.11, 2.20 | 2.20 | 2.65 / 21.47 |
| 1024 | 164.1 | 142.6 | 203.1 | 204.6 | 1.44, 1.42, 1.42 | 1.42 | 4.03 / 21.47 |
| 4096 | 560.0 | 540.0 | 595.4 | 594.0 | 1.12, 1.10, 1.10 | 1.10 | 4.03 / 21.47 |

The cs payload column is the median over both cs policies; cs heft moves almost nothing at low
intensity, data-local moves a little because it is work-conserving.

Reading: the speedup falls from 6.4x while both modes are I/O bound to 1.1x at 4096 iterations
and does not cross 1x. Repeats agree within about 2% except at i16 and i64, where one repeat
of cs was slower. From i256 up, cs data-local beats cs heft by 10-20%: HEFT moves 2.6-4 GB of
work off nodes it predicts are busy. From i256 up, the best disaggregated policy
stays 54-60 s slower than the best cs policy, which matches its 21.5 GB of payload at
8 x 47 MB/s (57 s): a single-slot worker never overlaps those transfers with compute
(FAIRNESS.md); the two-slot check planned for Saturday measures how
much of that gap overlap closes.
