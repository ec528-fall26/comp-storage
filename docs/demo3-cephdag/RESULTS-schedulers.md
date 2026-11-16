# New schedulers on the lab (stage B, cephdag8, sched-skew finished 2026-11-14 07:06 EST)

heft-cc (HEFT with booked send and receive links and a 5% move margin), backlog (move a task
off a node only while its local queue outlasts the move) and lp-plan (fluid LP solved once,
followed by a dispatcher), described in SCHEDULERS.md, against the existing policies.
cephdag `cbd1561`, cs mode, tbf 400 Mbit. Raw rows: aakashpc
`/mnt/mass/cephdag/campaigns/sched-{pilot,skew,uniform}/`.

Skewed placement (8 GiB, 65 of 128 shards on node 0), 3 repeats, 96 runs, all verified. Median
wall [min-max], median GB moved; the in-process fair-sharing forecast from SCHEDULERS.md in
brackets for the new policies.

| policy | i1 | i16 | i128 | i1024 |
|---|---|---|---|---|
| heft-cc | 18.3 [18.2-18.9], 2.35 GB (fc 20.2) | **20.2** [19.5-20.7], 2.62 GB (fc 22.8) | **35.1** [35.1-35.2], 3.02 GB (fc 37.7) | **95.4** [95.3-96.2], 4.50 GB (fc 93.8) |
| backlog | **17.6** [17.2-19.9], 0.67 GB (fc 20.2) | 22.2, 0.87 GB (fc 22.8) | 38.8, 1.34 GB (fc 38.9) | 108.3, 2.68 GB (fc 113.5) |
| lp-plan | 20.2, 0.81 GB (fc 20.2) | 24.0, 0.94 GB (fc 22.6) | 43.3, 1.61 GB (fc 39.1) | 114.9, 3.22 GB (fc 110.7) |
| greedy-wait | 20.6, 0 GB | 27.5, 0 GB | 54.6, 0 GB | 103.3, 3.83 GB |
| stealing | 21.9, 0.87 GB | 23.4, 0.94 GB | 40.1, 1.48 GB | 107.8, 2.75 GB |
| data-local | 23.2, 1.07 GB | 22.5, 1.34 GB | 39.3, 1.68 GB | 100.1, 2.89 GB |
| beam-4 | 27.5, 1.74 GB | 34.8, 1.95 GB | 37.7, 1.61 GB | 102.1, 3.02 GB |
| heft | 45.5, 2.68 GB | 39.2, 3.83 GB | 56.4, 3.89 GB | 108.1, 4.43 GB |

Reading. Booking link time turns HEFT from last to first: the same list scheduler with one
change is 2.5x faster at 1 iteration (45.5 to 18.3 s), wins at 16, 128 and 1024 iterations, and
is within 4% of the best at 1. It is the only policy within 5% of the best at every intensity,
so on this workload no intensity-specific choice is needed. backlog wins at 1 iteration by
moving least (0.67 GB) but falls to seventh at 1024. lp-plan never wins and is last at 1024
(114.9 s against an LP bound of 78.1 s): its once-solved plan is right in fluid terms but its
dispatcher loses the plan at whole-task granularity; not yet diagnosed. The fair-sharing
forecast ranked heft-cc first at 128 and 1024 and was within 10% for heft-cc and backlog
everywhere; it was optimistic for lp-plan at 128 (39.1 against 43.3).

Paired comparison only: this session's baselines ran 4-7% slower than the same cells in
demo3-skew the day before (greedy-wait i1 20.6 against 19.1, data-local i128 39.3 against
37.7), so compare policies within this table, not across campaigns.

Uniform placement (16 GiB, round-robin), 2 repeats, 20 runs, all verified (finished
2026-11-14 08:12 EST). Median wall, median GB moved:

| iterations | heft-cc | data-local | backlog | lp-plan | heft |
|---|---|---|---|---|---|
| 256 | 43.7, 0 GB | 43.9, 0.07 GB | 45.5, 0 GB | 46.5, 0.07 GB | 60.2, 5.17 GB |
| 1024 | 141.6, 0 GB | 141.8, 0 GB | 142.8, 0 GB | 142.0, 0 GB | 165.5, 8.05 GB |

With data spread evenly, heft-cc's 5% move margin keeps it from moving anything and it ties
data-local; plain HEFT moves 5-8 GB chasing calibrated read-rate differences between identical
nodes and loses 17-38%.

lp-plan2, the dispatcher fix from DIAGNOSIS.md section 2, measured at i128 and i1024: RESULTS-lp-plan2.md.
