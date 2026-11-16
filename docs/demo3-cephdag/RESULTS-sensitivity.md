# Sensitivity results: disagg2 and fused DROID (stage A, 2026-11-14)

runjob demo3-stage-a-20261114-003718 on aakashpc, exit 0, cephdag bb7de4b deployed to all 17 containers.
Campaigns: /mnt/mass/cephdag/campaigns/demo3-sensitivity-{sweep,droid}/ (rows.jsonl per step). All 26 rows verified.

## Synthetic sweep: 4 GiB of 64 MiB shards, independent, ratio 0.25, 2 repeats (mean wall seconds)

| intensity | cs heft | disagg fifo-hash | disagg2 fifo-hash | cs vs disagg | cs vs disagg2 |
|---|---|---|---|---|---|
| 1 | 3.07 | 18.91 | 16.41 | 6.16x | 5.34x |
| 256 | 12.07 | 27.68 | 19.54 | 2.29x | 1.62x |
| 4096 | 135.17 | 149.43 | 104.43 | 1.11x | 0.77x |

Repeats agree within 1-6%. Payload moved: cs 0, both disagg modes 5.37 GB.

## DROID shards 550-552 (2.74 GB source), one wave, 1 repeat (wall seconds)

| variant | cs data-local | disagg heft | disagg fifo-hash | disagg2 fifo-hash |
|---|---|---|---|---|
| unfused | 31.8 (0.42 GB moved) | 115.9 (22.3 GB) | 134.2 | 94.7 |
| fused | 22.6 (0.12 GB) | 36.8 (4.67 GB) | 34.1 | 22.5 |

## Reading

disagg2 is not only an overlap test. It runs 16 single-threaded workers (two per 2-CPU compute
container) against cs's 8 (one per storage node), so it also doubles compute concurrency. At
i4096 the transfer is about 14 s of disagg's 149 s, so disagg2's 30% gain there is mostly the
extra slots, not overlap; the same holds for fused DROID, which is compute-bound. cs with two
workers per storage node was not run, so the i4096 and fused-DROID comparisons against disagg2
are not slot-matched. Fusion removes the frames intermediate and with it most of the DROID gap
(disagg heft 115.9 -> 36.8 s), as FAIRNESS.md predicted. Stage B's demo3-overlap-sweep uses the
same cs (1 slot) vs disagg2 (2 slots) pairing.
