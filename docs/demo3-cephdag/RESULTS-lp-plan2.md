# lp-plan2 on the lab (stage D, cephdag8, sched-lp finished 2026-11-14 17:50 EST)

Question: is lp-plan's loss on the skewed graph its dispatcher, as DIAGNOSIS.md section 2 says?
lp-plan2 is lp-plan with two dispatcher changes, "wait for the link" and "node takes back its
own tasks"; the plan itself is unchanged. Falsification rule set before the run: if lp-plan2
does not bring i1024 below about 105 s, the link-feeding explanation is wrong.

Setup: sched-skew's 8 GiB (65 of 128 64 MiB shards on node 0), independent workload, ratio
0.25, i128 and i1024, 3 repeats, cs mode, cephdag `ac2f85d` (branch `lp-plan2`) deployed with
the tree-hash check, models matched SHA256SUMS, `verify cs` passed. 18 rows, all verified
(outputs digests 100450be... at i128, c5b014b3... at i1024, identical across policies).

| case | policy | wall s, median (repeats) | moved GB | forecast s |
|---|---|---|---|---|
| i128 | lp-plan2 | 33.4 (33.1, 33.4, 33.7) | 1.34 | 39.2 |
| i128 | heft-cc | 35.7 (35.3, 36.5, 35.7) | 3.02 | 37.6 |
| i128 | lp-plan | 41.8 (41.8, 43.8, 41.3) | 1.61 | 42.1 |
| i1024 | lp-plan2 | 88.6 (90.0, 88.6, 88.0) | 3.02 | 91.6 |
| i1024 | heft-cc | 95.0 (94.9, 95.0, 95.2) | 4.50 | 95.1 |
| i1024 | lp-plan | 114.9 (114.9, 118.0, 107.3) | 3.22 | 115.6 |

Planning time is 33-63 ms for all three. LP bound at i1024: 78.1 s (SCHEDULERS.md).

Result: the prediction held. lp-plan2 cuts lp-plan by 20% at i128 and 23% at i1024, and beats
heft-cc by 6.4% and 6.7% while moving 30-55% fewer bytes. At i1024 it is 13% above the LP
bound, against heft-cc's 22%. heft-cc and lp-plan reproduce sched-skew (35.1/95.4 and
114.9 s), so this campaign is comparable with RESULTS-schedulers.md. lp-plan2 beat its own
forecast at i128 by 15%, which the forecast does not explain yet.

Not covered at the time: lp-plan2 was not run at i1 or i16 on the skew graph or on uniform data,
where heft-cc is within 5% of the best and moves nothing. Filled on 2026-11-15, below.

Source: aakashpc /mnt/mass/cephdag/campaigns/sched-lp/*/a1/rows.jsonl; run log
~/runs/demo3-stage-d-20261114-133126/output.log on aakashpc.

## Low intensity and uniform data (sched-lp-low, sched-lp-uniform, finished 2026-11-15 16:28 ET)

Question: is lp-plan2's lead over heft-cc at i128/i1024 specific to high intensity, or does it hold
where heft-cc was best or tied (skew i1 and i16, uniform)? Plan before the run: the in-process
fair-sharing forecast tied them at i1 (20.2 s each, 12 moves off node 0 each) and put lp-plan2 2%
behind at i16 (23.3 against 22.8 s) and 1% behind on uniform i256 (44.1 against 43.7 s), so the
forecast expected no gain.

Setup: sched-skew's 8 GiB (65 of 128 64 MiB shards on node 0, own dataset `sched-lp-low-8g-64m`)
at i1 and i16, 3 repeats, cs lp-plan2, heft-cc, backlog, data-local; then uniform 16 GiB (64 MiB
shards round-robin, `sched-lp-uniform-16g-64m`) at i256, 2 repeats, cs lp-plan2, heft-cc,
data-local. Independent workload, ratio 0.25, cs models `calibration/cephdag8-cs` (as sched-skew),
deployed code mcts-lab `8cdc2b4`, specs on cephdag branch `sched-gaps` (`d7d3905`), runjob
`demo3-gaps` on aakashpc after sched-droid (RESULTS-sched-droid.md), one session, exit 0. 30 rows,
all verified; one output digest per intensity across policies and repeats.

Skew, median [min-max] of 3 repeats; each repeat over heft-cc in the same repeat; tasks moved off
node 0 (median); planning is at most 0.06 s for every policy:

| case | policy | wall s | over heft-cc (per repeat) | GB moved | moved off node 0 |
|---|---|---|---|---|---|
| i1 | lp-plan2 | **12.8** [12.7-13.8] | 0.72 (0.75, 0.67, 0.72) | 0.47 | 7 |
| i1 | backlog | 15.8 [15.7-17.3] | 0.89 (0.95, 0.83, 0.89) | 0.60 | 9 |
| i1 | heft-cc | 18.3 [17.7-19.0] | 1 | 2.35 | 12 |
| i1 | data-local | 21.4 [18.7-22.2] | 1.17 (1.02, 1.17, 1.21) | 1.28 | 14 |
| i16 | lp-plan2 | **16.0** [15.9-16.4] | 0.84 (0.84, 0.86, 0.84) | 0.60 | 9 |
| i16 | heft-cc | 19.0 [18.9-19.0] | 1 | 2.62 | 13 |
| i16 | backlog | 19.0 [18.9-20.3] | 1.00 (1.08, 1.00, 1.00) | 0.74 | 11 |
| i16 | data-local | 22.1 [21.2-22.9] | 1.17 (1.17, 1.12, 1.20) | 1.34 | 14 |

Uniform i256, 2 repeats:

| policy | wall s, median [min-max] | over heft-cc (per repeat) | GB moved |
|---|---|---|---|
| data-local | **44.2** [43.8-44.5] | 0.98 (0.94, 1.03) | 0.10 |
| heft-cc | 45.0 [43.4-46.7] | 1 | 0 |
| lp-plan2 | 45.9 [45.6-46.2] | 1.02 (0.99, 1.05) | 0.07 |

Result. On the skew graph lp-plan2 is the fastest policy in every repeat at i1 and i16, 28% and
16% faster than heft-cc and 19% and 16% faster than the next best (backlog), while moving the
fewest bytes (0.47-0.60 GB against heft-cc's 2.35-2.62 GB). With i128 and i1024 from sched-lp, it
is first at all four skew intensities measured. On uniform data the three policies are within
4% and within run-to-run spread (heft-cc's two repeats differ by 7.6%); lp-plan2 moved one task, as
its LP planned. lp-plan2 ran 37% and 31% under its forecast at i1 and i16 (12.8 against 20.2 s,
16.0 against 23.3 s).

Why lp-plan2 beat its forecast, from the per-task records (inference, not tested separately): the
LP plans 12 moves off node 0 at i1 and 14 at i16, using the model's 0.335 s per node-0 task at i1.
In the lab node 0 completed a task every 0.21-0.22 s at i1 under lp-plan2 (0.25-0.28 s under the
other policies; the worker's own stage timer says 0.15 s), so node 0 took back planned moves ("node
takes back its own tasks") and only 7 and 9 crossed its link. Node 0 finished its 58 tasks at
12.2-12.9 s and the runs ended 0.3-0.9 s later. heft-cc made all 12 of its planned moves at i1, and
its runs ended 4.4-5.8 s after node 0 had finished its own tasks (at 13.3-13.7 s), waiting for the
tasks shipped over node 0's link. heft-cc and lp-plan make every move they plan (in sched-lp 23 and
45 for heft-cc, 24 and 48 for lp-plan, at i128 and i1024); lp-plan2 made 20 of its 24 and 45 of its
48 there, which likely explains why it beat its i128 forecast by 15%. Why node 0 itself ran faster
under lp-plan2 was not established (one outbound transfer at a time, so less competition for node
0's disk, is a candidate). On DROID (RESULTS-sched-droid.md) lp-plan2's plan also executed fastest,
but its LP took 17-23 s to solve there.

Compare within these tables only: the same cells ran in sched-skew on 2026-11-14 (heft-cc i1 18.3,
i16 20.2; backlog 17.6, 22.2; data-local 23.2, 22.5) and here (18.3, 19.0; 15.8, 19.0; 21.4,
22.1); the host's storage drifts between days (DIAGNOSIS.md section 1).

Sources: aakashpc /mnt/mass/cephdag/campaigns/sched-lp-low/, sched-lp-uniform/ (durable copies
with matched checksums in ~/datasets/cephdag-demo3/campaigns/); run log
~/runs/demo3-gaps-20261115-201003/output.log on aakashpc; tables demo-data/t4_sched_lp_low.csv,
t4_sched_lp_uniform.csv.
