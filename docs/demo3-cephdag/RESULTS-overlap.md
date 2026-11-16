# demo3-overlap-sweep results (stage B, cephdag8, finished 2026-11-14 11:25 EST)

The full-sweep workload (16 GiB of 64 MiB shards round-robin, independent, ratio 0.25) with the
baseline run both ways in the same session: disagg (one worker per compute container) and
disagg2 (two workers per compute container, so transfers overlap compute). cephdag `cbd1561`,
tbf 400 Mbit, 2 repeats, 48 runs, all verified, one output digest per intensity. Raw rows:
aakashpc `/mnt/mass/cephdag/campaigns/demo3-overlap-sweep/*/a1/rows.jsonl`.

Median wall seconds; speedups pair the best policy of each mode within a repeat.

| iterations | cs data-local | cs heft | disagg fifo-hash | disagg2 fifo-hash | cs over disagg | cs over disagg2 |
|---|---|---|---|---|---|---|
| 1 | 14.0 | 13.6 | 71.4 | 64.7 | 5.30x | 4.79x |
| 16 | 15.2 | 14.4 | 74.2 | 65.3 | 5.16x | 4.54x |
| 64 | 20.7 | 23.5 | 81.8 | 68.3 | 3.96x | 3.31x |
| 256 | 47.5 | 61.0 | 107.2 | 73.4 | 2.26x | 1.55x |
| 1024 | 144.0 | 168.8 | 206.9 | 119.8 | 1.44x | 0.83x |
| 4096 | 540.3 | 557.1 | 596.8 | 390.5 | 1.10x | 0.72x |

Reading. While the work is I/O-bound (1-64 iterations) a second baseline worker helps little
(71.4 to 64.7 s at 1 iteration): the baseline is limited by its links, and computational storage
stays 3.3-4.8x ahead. From 256 iterations up, disagg2 pulls ahead of disagg by far more than its
transfer time (at 4096 it saves 206 s, against about 57 s of transfers), because it also has
twice the worker processes: 16 against cs's 8, on the same CPU quotas. So the crossover below
1x at 1024 and 4096 is not a fair result; stage C's cs2 (two workers per storage container)
against disagg2 is the slot-matched comparison.

Not comparable with demo3-sweep's walls: cs ran 13-24% slower here than on 2026-11-13 at low
intensity (cs heft 13.6 against 11.1 s at 1 iteration) while disagg was unchanged (71.4 against
72.3), which is the storage slowdown in DIAGNOSIS.md section 1. Ratios are paired within this
campaign; the cs-over-disagg column (5.30x at 1 iteration against 6.43x on 2026-11-13) is lower
for that reason.
