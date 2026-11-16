# Slot-matched check: cs2 against disagg2 (stage C)

Run: runjob `demo3-stage-c-20261114-130648` on aakashpc, finished exit 0 at 2026-11-14 17:10 EST
(log `~/runs/demo3-stage-c-20261114-130648/output.log` on aakashpc). cephdag 3bc0c28 (branch cs2)
deployed to all 17 cephdag8 containers; cs2 models sha256 b867a596… matched the ones derived on
JarvisVM; `verify cs` passed (377-380 Mbit per link). Campaigns `demo3-slots-droid` (1 wave, 6 runs)
and `demo3-slots` (16 GiB independent, 2 repeats, 1 at i4096; 27 rows, cs2 with data-local and heft). Every row's output
verification passed. Raw rows: `/mnt/mass/cephdag/campaigns/demo3-slots*/*/a1/rows.jsonl`.
Both modes have 16 worker slots: cs2 runs two workers in each storage container, disagg2 two in
each compute container.

## Independent workload, 16 GiB, 256 tasks (median wall seconds)

| iterations | cs2 data-local | cs2 heft | disagg2 fifo-hash | disagg2 / cs2 data-local |
| --- | --- | --- | --- | --- |
| 1 | 15.2 (16.9, 13.5) | 11.6 | 64.2 (60.9, 67.5) | 4.22x |
| 64 | 16.2 (17.7, 14.6) | 12.9 | 65.8 (64.7, 66.9) | 4.06x |
| 256 | 31.2 (33.5, 28.9) | 31.1 | 71.8 (70.8, 72.8) | 2.30x |
| 1024 | 97.6 (94.9, 100.4) | 95.0 | 118.9 (122.1, 115.7) | 1.22x |
| 4096 | 364.8 (1 run) | 362.6 | 392.4 (1 run) | 1.08x |

disagg2 moves 21.47 GB in every cell; cs2 moves 0-0.27 GB.

Against the overlap sweep (single-slot cs, same 16 GiB workload, earlier campaign): cs 540.3 s at
i4096 and 144.0 s at i1024, so the second worker per storage node cuts cs by 32-33% at high
intensity. disagg2 reproduced across campaigns (390.5 s then, 392.4 s now at i4096; 119.8 / 118.9 s
at i1024), which makes this cross-campaign comparison less risky than most, though DIAGNOSIS.md
warns host storage drifted over the day.

## DROID wave 0, 352 tasks unfused / 88 fused, 1 run each

| pipeline | cs | cs2 | disagg2 | disagg2 / cs2 |
| --- | --- | --- | --- | --- |
| unfused | 32.9 s (0.47 GB moved) | 21.2 s (0.69 GB) | 91.0 s (22.28 GB) | 4.29x |
| fused | 22.4 s (0.12 GB) | 13.1 s (0.08 GB) | 23.5 s (4.67 GB) | 1.79x |

## Reading

With slots matched, the high-intensity crossover from the overlap sweep (cs over disagg2 0.83x at
i1024, 0.72x at i4096) disappears: cs2 is ahead at every intensity, and the margin shrinks toward
1x as compute dominates, as the I/O-ratio argument predicts. The earlier sub-1x cells were the
slot mismatch (16 workers against 8), not a property of computational storage. At i1024 and
i4096 the absolute gap is 21-28 s, about half the 54-60 s tail of the single-slot sweep, consistent
with disagg2 overlapping part of its transfers. On fused DROID, the slot-matched advantage is
1.8x, not the tie stage A showed; that rests on one run.

Not separated: whether the 8 DRAM-bus-sharing workers per host limit cs2 differently from disagg2
(both run 16 processes on one machine); i4096 and the DROID cells are single runs; cs2 heft beats
cs2 data-local by 3-4 s at i1-i64 while moving nothing, which is within the run-to-run spread
seen in data-local itself (13.5-17.7 s) and not interpreted.
