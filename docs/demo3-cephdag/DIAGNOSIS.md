# Diagnosis: DROID data-local drift, lp-plan at i128/i1024, greedy-wait planning time

Written 2026-11-14 from the per-run result files alone. No runs, no lab or cephdag changes. The data
are copies of aakashpc `/mnt/mass/cephdag/campaigns/{demo3-droid,demo3-skew,sched-skew}` and
`/mnt/mass/cephdag/calibration/cephdag8-cs/models.json` (sha256 `30f993fb…`, the file the runs
embed). Every number below is printed by one of the scripts in `demo3/analysis/` (commands at the
end). "Measured" means read from the per-task records; "forecast" means the in-process simulation
of `sched_forecast.py`; "estimated" means arithmetic on measured numbers, said where it is used.

A run file records, per task, the worker, the coordinator's submit and completion times, the
worker's read, compute and write stage seconds, and the output pools; per decision, its end time
and length. A task's input location is rebuilt from the dataset in `config.json` and the producer's
recorded outputs.

## 1. Why data-local's seconds per source GB rose across the DROID batches

**Answer.** The storage path got slower, on every node at once, and data-local is the configuration
whose wall is almost pure local I/O plus compute. It is not placement and not dispatch. From batch
0 to batch 6, data-local's compute stayed flat while every storage-bound part of a task slowed:
local frames reads 379 → 243 MB/s (−36 %), source reads 359 → 263 MB/s (−27 %), decode writes
410 → 324 MB/s (−21 %), and the per-task slack outside the worker's stage timer, which includes the
worker's two RADOS journal writes per task (`worker.py` `_start` line 253 and `_execute` line 299),
19 → 34 ms. These account for +1.55 of the +1.64 s/GB rise. The same slowdown is still there a day
later: the skew campaign rerun after DROID reads and writes 9-30 % slower than the same cells before
it, with unchanged worker and storage code.

| batch | data-local s/GB | frames read MB/s | source read MB/s | decode write MB/s | slack ms/task | worker busy % | tail s | gaps s/worker | beam-4 s/GB | beam-4 frames read MB/s |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 10.26 | 379 | 359 | 410 | 19.2 | 98.9-99.5 | 2.4 | 4.2 | 12.72 | 312 |
| 1 | 10.76 | 325 | 319 | 415 | 25.8 | 98.4-99.6 | 3.0 | 5.7 | 12.42 | 315 |
| 2 | 11.29 | 280 | 328 | 367 | 31.5 | 98.2-99.3 | 3.5 | 7.5 | 12.34 | 320 |
| 3 | 11.51 | 270 | 279 | 342 | 30.9 | 98.4-99.3 | 2.5 | 7.5 | 12.66 | 304 |
| 4 | 11.56 | 268 | 280 | 337 | 32.1 | 97.7-99.4 | 2.2 | 8.3 | 12.64 | 296 |
| 5 | 11.75 | 254 | 259 | 330 | 33.3 | 97.7-99.5 | 2.0 | 8.0 | 12.95 | 279 |
| 6 | 11.90 | 243 | 263 | 324 | 33.5 | 97.9-99.4 | 4.0 | 8.0 | 13.06 | 279 |

Decomposition of the rise, worker seconds per source GB summed over the 8 workers and divided by 8
(batch 0 → 6): frames reads +0.80, writes +0.39, source reads +0.13, slack +0.23, dispatch gaps
+0.07, compute −0.10 (55.1 → 54.4 summed CPU-s/GB). The sum of all parts over 8 tracks the wall
within 0.2-0.3 s/GB in every batch, so nothing large is missing from it.

What is ruled out, and how:

- **Placement imbalance.** The partitions are spread evenly in every batch: 301-307 per pool in
  batch 0, 258-265 in batch 6; the largest pool holds 1.015-1.033 of the mean source GB with no
  trend. data-local ran 15-51 of about 2,400 decodes off their node per batch, with no rising
  trend. Tasks per worker spread about the same in every batch.
- **The tail.** The time from the first worker going idle for good to the last task finishing is
  2.0-4.0 s per batch (0.2-0.5 % of the wall), with no trend. Removing it changes the speedup over
  disaggregated HEFT from 4.17x to 4.19x in batch 0, 3.56x to 3.58x in batch 6, and 3.76x to 3.78x
  over all 496 GB.
- **Dispatch.** Every worker is busy 97.7-99.6 % of the wall in every batch. Dispatch gaps rise from
  3.5 to 7.6 ms per task, 0.07 s/GB in all.
- **One slow node.** Frames reads slow identically on all eight nodes: 377-383 MB/s per node in
  batch 0, 238-248 in batch 6.
- **Compute.** Flat or slightly faster for data-local, flat for beam-4 (54.0 → 53.9) and for
  disaggregated HEFT (50.7 → 50.6), which runs on other containers.

Why HEFT is flat and beam-4 rose only 3 %: disaggregated HEFT reads and writes over 47 MB/s links,
far below the OSDs' rates (185 summed s/GB of remote reads in every batch), so it cannot see a slower
disk. beam-4 sees the same slowdown, but less of it: its frames reads were already slower in batch 0
(312 MB/s) and fell 11 % to 279, while data-local fell 36 % from 379. Its wall also carries a
constant 11-12 % of dispatch gaps between tasks (84-92 ms per task) that do not track storage. beam-4's own
parts add up to +0.34 s/GB, its measured rise.

The drift has two parts. From batch 3 on, both cs policies lose about 3 % of frames-read rate per
batch (data-local 270 → 243, beam-4 304 → 279 MB/s over three batches). On top of that, data-local
had a fast regime early on that it lost: its frames reads ran at 377-430 MB/s from 10 % to 80 % of
batch 0 and for the first 40 % of batch 1, and at 210-300 MB/s from the middle of batch 1 on;
beam-4 reached about 410 MB/s only in the first tenth of batches 1 and 2.

| local frames read MB/s by tenth of the run | 0-10 % | 20-30 % | 40-50 % | 60-70 % | 80-90 % | 90-100 % |
|---|---|---|---|---|---|---|
| data-local batch 0 | 337 | 395 | 424 | 421 | 343 | 280 |
| data-local batch 1 | 385 | 401 | 327 | 282 | 274 | 274 |
| data-local batch 2 | 340 | 267 | 284 | 288 | 280 | 281 |
| data-local batch 6 | 251 | 229 | 258 | 249 | 257 | 230 |
| beam-4 batch 0 | 301 | 301 | 332 | 319 | 312 | 323 |
| beam-4 batch 1 | 412 | 275 | 302 | 306 | 314 | 329 |

Run order does not explain it: data-local ran second in batches 0, 1 and 6 and first in 2-5. Nor
does read-after-write freshness: in every batch more than 99 % of data-local's frames reads start
within 3 s of the decode that wrote them finishing, and the rate fell in each age bin (0-1 s:
368 → 227 MB/s; 1-3 s: 400 → 268). Read rates do fall with the number of other read or write stages
running on the host (data-local batch 0: 425 MB/s with 0-1 others, 263 with 6-7), as expected
when all 8 OSDs share one disk, and later batches have more overlap, but at matched concurrency
data-local still fell 25-43 % from batch 0 to batch 6 and beam-4 6-27 %:

| frames read MB/s, by other I/O stages running | 0-1 | 2-3 | 4-5 | 6-7 |
|---|---|---|---|---|
| data-local batch 0 | 425 | 373 | 313 | 263 |
| data-local batch 6 | 320 | 263 | 199 | 150 |
| beam-4 batch 0 | 370 | 312 | 266 | 213 |
| beam-4 batch 6 | 346 | 290 | 228 | 156 |

The slowdown outlived the DROID campaign (`storage_before_after.py`): the same skew cells, same
policies, 3 repeats each, before DROID (demo3-skew, 2026-11-13) and after it (sched-skew,
2026-11-14). `worker.py`, `storage.py` and `functions.py` are identical in the two deployments
(cephdag `2b22dd6` and `cbd1561`); they differ in policies, `run.py`, cluster files, the fused DROID
path in `droid.py` (not used by skew) and the coordinator's byte counting.

| cell | local read MB/s, before → after | local write MB/s, before → after | wall s, before → after |
|---|---|---|---|
| greedy-wait i1 | 475 → 394 (−17 %) | 503 → 413 (−18 %) | 19.1 → 20.6 |
| data-local i128 | 446 → 342 (−23 %) | 467 → 352 (−25 %) | 37.7 → 39.3 |
| beam-4 i1024 | 423 → 343 (−19 %) | 481 → 391 (−19 %) | 101.2 → 102.1 |
| all 20 cells | −11 % to −30 % | −9 % to −29 % | |

This most likely accounts for the "baselines ran 4-7 % slower than in demo3-skew" note in
RESULTS-schedulers.md: the code the baselines ran did not change, the storage rates did.

**Not established: the mechanism inside the storage stack.** Host facts, read without root on
2026-11-14: the root filesystem holding the Docker volumes is btrfs mounted
`compress=zstd:3,ssd,discard=async` (copy-on-write) on a Samsung 990 PRO 4 TB; it is 85 % full, 582 GB
free against 1,017 GB at lab validation on 2026-11-12, and the OSDs are 56 GiB sparse block files
on it (LAB-VALIDATION.md). A plausible story is that the sparse OSD files filled in and fragmented
under copy-on-write and compression as the campaigns wrote through them; the records cannot show
that, and the 435 GB fall in free space is not attributed to the OSD files.
A squeezed page cache is not shown either way: read on 2026-11-14 after the campaign, n1 and n7
hold 1.0-1.2 GB of file cache and 1.05 GB anonymous against their 6 GiB limits, while n0 is at
5.8 of 6 GiB (3.4 GB file, 2.3 GB anonymous); per-container memory during the DROID batches was
not recorded. Why data-local had the early fast regime and beam-4 mostly did not is open.

Checks, in order of cost: (1) as root, `compsize` and `filefrag` on the eight OSD block files and
`btrfs filesystem usage /`; (2) a 64 MiB local read and write timing on one node now against the
calibrated rates in `models.json` (read 264-392 MB/s); (3) to confirm the effect on the result,
rerun DROID batch 0 with data-local alone: about 768 s if the storage recovered, about 890 s
(11.9 s/GB) if it did not.

**What it means for the demo.** The cs/disagg DROID ratio is understated in the later batches by
the storage drift, not overstated: at batch 0's 10.26 s/GB every batch would give 4.12-4.17x, and
the 496 GB total 4.14x instead of the measured 3.76x (estimated, from the measured disagg walls).
Comparisons within one campaign are paired and unaffected; comparisons of walls across campaigns
from different days are not safe.

## 2. Why lp-plan lost at i128 and i1024

**Answer.** Mainly the dispatcher, with a plan that leaves it no slack and one rate the model gets
wrong. The dispatcher ran exactly the planned counts (every repeat, every intensity), but it feeds
node 0's link badly: it moves one shard at a time, a lone flow reaches 42 MB/s rather than the
47 MB/s the LP charges, and the link sits idle whenever no receiving worker is free at the moment
it clears, because a worker whose move finds the link busy starts a local task instead. At i1024
the link was idle 24-30 s that way, and node 0 then idled for its last 38-41 s, having finished its
planned 17 tasks while 18-21 moves still had to start, which the dispatcher forbids it to take. The
fluid LP's T is a valid bound, but it plans the link at 88 % (i1024) and 99 % (i128) of T, so these
losses go straight to the wall. At i128 the LP's vertex solution sends all 24 moves to workers 6 and 7, leaving workers 1-5
idle from about 8 s.

The plan, rebuilt in-process with `PlacementLp` on each run's own graph and models
(`lp_plan_dispatch.py`), and what ran (medians and ranges over 3 repeats; full table per repeat in
the script output):

| | lp-plan i128 | heft-cc i128 | lp-plan i1024 | heft-cc i1024 |
|---|---|---|---|---|
| wall s | 43.3-43.6 | 35.1-35.2 | 112.8-115.7 | 95.3-96.2 |
| LP T, planned link busy | 34.6 s, 34.2 s | | 78.1 s, 68.4 s | |
| tasks from node 0 per worker (executed) | 41, 0, 0, 0, 0, 0, 11, 13 (as planned) | 42, 11, 0, 0, 0, 0, 0, 12 | 17, 7, 7, 7, 7, 7, 7, 6 (as planned) | 20, 11, 6, 4, 0, 9, 9, 6 |
| other moves | 0 | 22 | 0 | 22 |
| first move off node 0 | 0.1 s | 0.1 s | 4.4-4.6 s | 0.1 s |
| node 0 link active, moves in flight, MB/s | 37.7-38.0 s, 1.00, 42.4-42.7 | 33.3-33.4 s, 1.55-1.56, 46.3 | 76.7-76.9 s, 1.00, 41.9-42.0 | 66.7-67.1 s, 2.1-2.3, 45.0-45.3 |
| one move's read s (model 1.425) | 1.53-1.60 | 2.22-2.27 (shared) | 1.60 | 3.03-3.23 (shared) |
| link idle between moves: all due receivers busy / one free | 3.3-4.8 / 0.2-1.6 s | | 23.6-29.7 / 0.6-3.8 s | |
| node 0's last finish | 31.4-31.6 s | 32.0-33.1 s | 73.5-74.8 s | 84.8-88.0 s |
| moves started after node 0 finished | 5-6 | 0-1 | 18-21 | 1-2 |
| worker last finishes s (repeat 0) | 31, 7, 7, 7, 8, 7, 37, 43 | 32, 32, 18, 20, 19, 18, 18, 35 | 75, 85, 83, 86, 88, 113, 108, 116 | 85, 95, 84, 90, 87, 93, 94, 93 |

How the i128 wall adds up: 24 moves at 1.57 s each keep the link busy 37.7 s instead of the planned
34.2 s (+3.5 s from the lone-flow rate), the link is idle 4.9 s between moves, mostly while both
receivers are computing or running their own 9 tasks, so the last move ends at 42.7 s and the task
at 43.3 s. heft-cc also sends its node-0 moves to two workers, but keeps 1.55-1.56 moves in flight on
average, so the link delivers 46.3 MB/s and is busy 33.3-33.4 s.

How the i1024 wall adds up: every worker starts with a local task (at the first position the lag
rule ranks a worker's 9 local tasks above its 7 moves), so the first move starts at 4.4-4.6 s. The
seven receivers then free up together every 4.4 s; one takes the link, and for the others the move
class is skipped because the link is busy (`lp.py` lines 191-192) and they start a 4.3 s local task.
So until about 45 s the link carries one 1.6 s move per 4.4 s cycle, with gaps of 2.6-3.7 s in which
every receiver due a move is computing. Once the receivers run out of local work (their last local
task starts at 41-59 s) the link is saturated and they wait for it in turn: in repeat 0 workers 5-7
wait from about 45-57 s to 84-87 s and start their last moves at 103-110 s, and node 0 finishes its 17
tasks at 74 s and idles for 41 s. Repeat 0 adds up as first move 4.6 s + link busy 76.8 s + link
idle 30.3 s + the last moved task's compute and write, 4 s = 115.7 s.

**The model's rates are not the main cause.** Local tasks are overpredicted (0.85 s model against
0.73-0.78 s measured at i128, 4.47 against 4.19-4.34 at i1024), moved tasks' compute is right, and a lone
transfer is underpredicted (1.425 s against 1.58-1.60 s). A forecast that keeps the calibrated
models and only caps a lone transfer on node 0's link at the measured 42.5 MB/s reproduces lp-plan's
walls: 42.1 s against 43.3 measured at i128, 115.6 s against 114.9 at i1024. So whole-task
execution of this plan by this dispatcher explains the loss. The lone-flow rate adds 3.5 s (i128)
and 8.4 s (i1024) of link time, and 3.0 s and 4.9 s of forecast wall (fair sharing 39.1 → 42.1 s,
110.7 → 115.6 s).

Also ruled out: planning cost (lp-plan spends 0.03-0.04 s in `choose` per run, and node 0's dispatch
gaps are the lowest of all policies, 17-21 ms per task), and coordinator latency on moves (at most
0.2-3.8 s of link idle time had a free receiver).

**Smallest change that tests it.** In `LpPlan._planned` (`lp.py` lines 181-196), take the most
lagging class first and only then check the link: if it is a move whose link is busy, return None
so the worker waits for the link, instead of skipping to its next class (lines 191-192; a local
task that holds the worker for 4.3 s at i1024). About three lines. A second
change of about 15 lines tests the end phase: once a worker's own plan is done, it may take an
unstarted task of its own node's class that the plan gave to someone else. Both are written only as
subclasses inside `demo3/analysis/lp_plan_dispatch.py` for the forecast; nothing in cephdag is
changed. Forecast with the lab-like link (lp-plan's own walls reproduced above):

| case | lp-plan | wait for link | take back | both | heft-cc |
|---|---|---|---|---|---|
| i1 | 20.2 s | 21.8 s | 20.2 s | 20.3 s | 20.2 s |
| i16 | 23.8 s | 25.5 s | 23.8 s | 23.8 s | 22.8 s |
| i128 | 42.1 s | 41.3 s | 38.4 s | 39.2 s | 37.6 s (35.1 measured) |
| i1024 | 115.6 s | 99.5 s | 104.0 s | 91.6 s | 95.1 s (95.4 measured) |

The plain fair-sharing forecast (the model's own rates) shows the same dispatcher loss: lp-plan
110.7 s at i1024 against an LP T of 78.1 s and heft-cc's 93.8 s, and 89.2 s with "wait for link".
So the loss does not need the lone-flow rate error to appear. "Wait for link" alone costs 1.6-1.7 s
at i1 and i16 in the lab-link forecast (a receiver waits for the link when it could have run local
work); "both" does not.

A falsifiable test, if Aakash wants it: sched-skew i128 and i1024, 3 repeats, lp-plan against
lp-plan with "both", with heft-cc as the reference; about 40-50 min of lab time. If "both" does not
bring i1024 below about 105 s, the link-feeding explanation is wrong. At
i128 neither variant reaches heft-cc in the forecast: there the plan's 24 moves at the lone-flow
rate need 37.7 s of link time by themselves, so the plan, not the dispatcher, sets the floor; letting
two moves share the link (as heft-cc does) or planning with the lone-flow rate (fewer moves) is the
corresponding plan-side test. These are scheduling-policy changes and Aakash's call.

## 3. Is greedy-wait's planning on the critical path?

**Answer: yes, for most of it.** greedy-wait does not plan once: it makes 287-356 decisions per run
(median per intensity; 159-228 of them waits) of 51-74 ms each on average (longest 0.10-0.33 s), and
the coordinator's loop is serial (poll, observe, choose, dispatch or wait; `coordinator.py`
`_schedule` line 330, `choose` at line 397), so nothing is dispatched while `choose` runs. Node 0's
worker, which runs all 65 of node 0's tasks at i1-i128 and finishes last, waited 8.1 s in gaps
between its tasks at i1, 7.0 s of it while the coordinator was inside `choose`. Its gap per task is
103-125 ms against 19-73 ms for every other policy.

| iterations | greedy-wait wall s | policy s | node 0: executing s, gaps s (ms per task), inside `choose` s | other policies' node 0 gap, ms per task |
|---|---|---|---|---|
| 1 | 20.6 | 14.7 | 12.6, 8.1 (124), 7.0 | 21-69 |
| 16 | 27.5 | 17.9 | 19.4, 8.1 (125), 6.8 | 20-69 |
| 128 | 54.6 | 21.3 | 47.9, 6.7 (103), 5.8 | 19-73 |
| 1024 | 103.3 | 26.4 | 99.1, 4.3 (186), 3.7; the last worker (6 or 2) 14.1 s of gaps, 6.6 s inside `choose` | 17-38 |

Medians of 3 repeats (`planning_critical_path.py`). Estimated: with the 46-58 ms per task of backlog, stealing, heft-cc and
data-local, node 0 would finish at i1 near 15.6-16.4 s instead of 20.6 s, ahead of every measured
policy (backlog 17.6 s). At i128 the planning costs about 3.5-4.5 s of 54.6 (node 0's 6.7 s of gaps
against 2.3-3.3 s at 35-50 ms per task); greedy-wait's loss there is its choice to move nothing (node 0's
own work alone is 47.9 s). PILOTS.md's "overlapped with execution" (lines 169 and 356) is right only in
that running tasks keep running; the next task of the critical worker waits for the decision in
progress.

## Scripts and data

Run with the system `python3 -I` (numpy 2.5.4 on the VM); each takes the copied campaign
directory as its argument.

- `demo3/analysis/droid_drift.py CAMPAIGN_DIR [OUT_JSON]`: question 1, all DROID tables (placement,
  busy, tail, gaps, stage seconds per GB, storage rates by batch, tenth of run, node and concurrent
  I/O, read age, speedups).
- `demo3/analysis/storage_before_after.py CAMPAIGNS_DIR`: question 1, demo3-skew against sched-skew.
- `demo3/analysis/lp_plan_dispatch.py SCHED_SKEW_DIR MODELS_JSON [CEPHDAG_DIR]`: question 2, the
  rebuilt plan, per-repeat lp-plan and heft-cc schedules, and the variant forecasts. Imports
  cephdag `schedulers` (`cbd1561`) and `demo3/sched_forecast.py` read-only; about 2 s.
- `demo3/analysis/planning_critical_path.py SCHED_SKEW_DIR`: question 3, all policies.

The raw copies are in `/home/aakash/.hermes/cache/scratch/diagnosis/raw/` (466 MB, pruned after a
day; re-copy with `ssh aakashpc "sh -c 'cd /mnt/mass/cephdag && tar cf - campaigns/demo3-droid
campaigns/demo3-skew campaigns/sched-skew calibration/cephdag8-cs/models.json'" | tar xf -`).

Open: the storage mechanism (needs root on aakashpc); why data-local alone had the early fast
storage regime; whether the two lp-plan variants behave in the lab as forecast (not run).
