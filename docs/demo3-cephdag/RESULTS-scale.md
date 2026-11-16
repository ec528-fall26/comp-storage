# Scale check: independent workload at 256 GiB

Campaign demo3-scale, the last step of runjob demo3-stage-b on aakashpc (cephdag cbd1561), run
2026-11-14 11:25-15:30 EST. Synthetic dataset demo3-synthetic-256g-64m (4,096 shards of 64 MiB,
spread evenly over the 8 storage nodes), output ratio 0.25, 1 repeat, all 6 runs verified (one
output digest per intensity, equal across modes). Records:
/mnt/mass/cephdag/campaigns/demo3-scale/*/a1/rows.jsonl on aakashpc.

| iterations | cs data-local | cs heft | disagg fifo-hash | cs over disagg (data-local) | payload moved GB, cs data-local / cs heft / disagg |
|---|---|---|---|---|---|
| 1 | 233.8 s | 227.8 s | 1139.3 s | 4.87x | 2.9 / 4.9 / 343.6 |
| 256 | 736.9 s | 1175.3 s | 1631.9 s | 2.21x | 0.9 / 157.2 / 343.6 |

Against the 16 GiB sweep (RESULTS-sweep.md, 16x less data): the baseline scales linearly at both
intensities (15.8x and 15.5x), and so does cs at 256 iterations (15.4x), so the 2.2x speedup holds.
At 1 iteration cs grows 19-21x, so the speedup falls from 6.43x to 4.87x. Not yet separated: the
16 GiB runs may have read partly from cache (2 GiB per node against 32 GiB here), or the host's
local storage was slower on this day (DIAGNOSIS.md: reads and writes 9-30% slower after DROID).

Plain HEFT on uniform data at 256 iterations moved 157 GB and ran 59% behind data-local (at
16 GiB it moved 2.65 GB and ran 26% behind), the same link-blind behaviour seen on skew; heft-cc
was not part of this campaign.

Per-step wall time (3,521 s for the two cs runs at i1, against 462 s measured) is probably the
final-output verification read (64 GiB of outputs per run at roughly 50 MB/s); inferred, not timed.
