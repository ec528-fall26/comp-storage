# Three contention-aware schedulers: heft-cc, backlog, lp-plan

Written 2026-11-13 for the demo. Code on branch `schedulers` of `~/projects/comp-storage/cephdag`
(from demo3 `bb7de4b`); nothing here has run on the lab yet. The numbers below are in-process
forecasts with the lab's calibrated models (`/mnt/mass/cephdag/calibration/cephdag8-cs/models.json`,
sha256 `30f993fb…`, the file the measured runs used) next to the measured skew and sweep walls.

## The problem they attack

Skewed placement: 128 shards of 64 MiB, 65 on node 0 and 9 on each of nodes 1–7, one task per
shard, every node both stores and computes, one 400 Mbit link per node (about 47 MB/s, so 1.4 s
per shard). Node 0 alone needs about 65 × 0.29 s = 19 s at 1 iteration; everyone else is done
after 3 s. Moving a task off node 0 frees 0.29 s of node 0's time but costs 1.4 s of node 0's
one send link, and all moves out of node 0 queue on that link. HEFT plans every transfer as if
it had the link alone (`min(read, tx, rx)` per transfer, no booking), so at i1 it plans 31
moves off node 0 and a makespan of 11.5 s, while node 0's link needs 31 × 1.4 s = 44 s to carry
them: it is last at every intensity (45.2 s at i1 against greedy-wait's 19.1 s). The three policies below each account for the link in a different way:
by booking it (heft-cc), by comparing queues (backlog), or by solving for the whole placement at
once (lp-plan).

## heft-cc: HEFT with booked links

The rule is HEFT's: rank tasks by upward rank, and place each, in rank order, on the worker
(and output targets) where it would finish earliest. The only change is what "finish" means.
Beside the calendar of each worker there is now a calendar per node send link and per node
receive link (the same insertion calendar class HEFT already uses). Placing task t on worker w
walks its stages in order: each input stored on another node s is a transfer of size/rate
seconds that may start only when both s's send calendar and w's receive calendar have that much
free time; the compute follows the last input; each output written to another node is a
transfer booked on w's send link and the target's receive link. The worker is held from the
first stage to the last, so a task waiting for a busy link also waits on its worker. Two
transfers out of node 0 can therefore never overlap in the plan, and the seventh move out of
node 0 is planned to arrive 7 × 1.4 s after the first. Output placement is HEFT's (the target
options the state offers; hashed by the storage system on role-split clusters).

On the skew graph at low intensity the booked link makes moves expensive as soon as a few are
queued, so heft-cc moves 12 tasks off node 0 instead of HEFT's 31 and plans 17.7 s instead of
HEFT's (impossible) 11.5 s. At high intensity each task is long (about 4.4 s at i1024) and the
link is idle most of the time, so it moves more (45 off node 0 at i1024), which is right there.
A weakness of the HEFT order remains: once node 0's tasks are placed on nodes 1–7, some of those
nodes' own tasks finish earlier on a neighbour, so heft-cc also moves 23 tasks between the
lightly loaded nodes at i1 (2.35 GB moved in total against 0.81 GB for the other two policies).
Those moves use idle links and cost no time in the forecast, but they are bytes.

Planning cost: one plan at the first decision, about 0.1 s for 128 tasks (0.10–0.15 s per run
in the forecast) and 2.9 s for 4,096 tasks, against HEFT's 0.66 s; each candidate placement
now searches three calendars instead of one.

Implementation: `cephdag/policies/list.py`: `ContentionHeftPlan` at line 287 (placement 301,
the stage walk `booked_end` 316, a transfer on two link calendars `transfer_end` 328), the
policy `ContentionHeft` at 388; HEFT's loop is shared and calls `place` (lines 132–138), and
HEFT's own plan is unchanged (the equivalence tests pass, and the 4,096-task HEFT plan is
identical to bb7de4b's).

### The move margin (heft-cc only)

The calibrated read rates of the eight identical storage nodes range from 264 to 392 MB/s. On
uniform data HEFT reads that as "node 3 is faster" and moves work to it: the forecast has HEFT
and (without a margin) heft-cc moving 77 of 256 tasks, 5.17 GB, at uniform i256, taking 55.4 s
against 43.7 s for keeping everything local, which is what the lab measured for HEFT
(5.17 GB, 60.3 s against data-local's 47.9 s). So a placement that moves bytes must finish 5%
earlier than one that does not (`MOVE_MARGIN = 0.05`, `list.py` line 19, used at line 135).
With it heft-cc moves nothing on uniform data and its skew forecast improves at i128 (41.2 →
37.7 s) and i1024 (109.5 → 93.8 s); 2% was not enough on uniform (0.20 GB still moved), 10% was
worse on skew i1024 (99.7 s). HEFT itself keeps no margin, as the baseline.

## backlog: move only while the backlog outlasts the move

A free worker runs a ready task whose input is on its own node if it has one. Otherwise it may
take a task whose data sits on another node s, only if

    slowdown(s) × (model seconds of the ready tasks queued on s)
        >  (bytes already in flight on s's send link) / (s's send rate)
           + (model seconds of this task on this worker, input read over the link)

The left side is how long s will still be busy with its own queue; the right side is when the
moved task would be finished here, given the transfers already queued on s's link. If moving
finishes the task before s would have reached it, the move is worth it. `slowdown(s)` is
measured: the actual time of the local tasks s has finished over their model time (1 until one
has finished), so a node the model flatters or underrates is corrected as the run goes. Of the
moves that pass, the one with the largest margin goes first. A node with no worker (a storage
node of a role-split cluster) has an endless backlog, so its tasks always go to compute nodes;
outputs go to the worker's own pool, or to the storage hash on role-split clusters. Each
decision looks at the ready queue once per free worker, with no lookahead.

On the skew graph at i1, node 0's backlog starts at 65 × 0.335 s = 22 s (model), and nodes 1–7
finish their own nine tasks after about 3 s; from then on each free worker moves a task while
the queue on node 0's link plus the task is shorter than node 0's remaining queue. It stops
after 12 moves (0.81 GB): the link queue grows by 1.4 s per move while node 0's backlog shrinks.
On uniform data no node ever has a backlog longer than a transfer, so it moves nothing. At high
intensity its "local first" rule is its weakness: nodes 1–7 spend their first 40 s (9 × 4.4 s at
i1024) on their own tasks while node 0's link sits idle, and the moves then come too late:
113.5 s against data-local's 101.8 s in the forecast. That is the rule as specified; starting
moves before the local queue empties would be a different policy.

Planning cost: under 20 ms per whole run in the forecast (0.007–0.018 s).

Implementation: `cephdag/policies/backlog.py`: the rule in `Backlog.choose` lines 32–63 (the
inequality at 53–58), the left side `_backlog` 82, the right side `_moved_finish` 90, the
slowdown measurement `_measure` 110; `local_targets` (line 6) is the output rule shared with
lp-plan.

## lp-plan: a fluid LP solved once, and a dispatcher that follows it

At the first decision, the tasks are grouped into classes: tasks with the same function and
parameters whose inputs are on the same nodes (on the skew graph, 8 classes: one per node).
For class c with n_c tasks and worker w, the calibrated models give s[c,w], the seconds one task
of c takes on w (read, compute, write in sequence, as a worker runs them), busy[c,w,r], the
seconds it keeps resource r busy (r is the read side or write side of a node's disk, or a node's
send or receive link: bytes through r over r's rate), and m[c,w], its bytes over the network.
With x[c,w] ≥ 0 the number of class-c tasks run on w:

    minimize T  subject to
        Σ_w x[c,w]               = n_c   for every class c          (every task runs)
        Σ_c x[c,w] · s[c,w]      ≤ T     for every worker w         (no worker works past T)
        Σ_c,w x[c,w] · busy[c,w,r] ≤ T   for every disk side and link r

T is a lower bound on the makespan for this placement of work, since every worker, disk and link
must fit its share inside T. Many placements reach the least T, so a second LP fixes T at that
value and minimizes Σ x[c,w] · m[c,w], the bytes moved. The fractional x is rounded to whole
counts per class, keeping the largest fractional parts. A small hand-solved case (it is a test):
ten 1-second tasks on node 0 and an idle node 1, where a moved task also needs 1 s on the link,
gives x0 ≤ T, 2·x1 ≤ T, x1 ≤ T, x0 + x1 = 10, so T = 20/3, x0 = 20/3, x1 = 10/3, rounded 7 and 3.

The dispatcher then follows the counts. A free worker takes the next ready task of the class in
which it lags its planned mix most (class c on w is due count[c,w] × (tasks w has started + 1)
/ (tasks planned for w), and lags by that less what w has started of c). A planned task whose
input must cross the network waits while its source's send link is busy, so each link carries
one planned transfer at a time at full rate, as the LP assumes. A worker whose plan is done
does not take tasks planned for another. Tasks the LP does not cover, those that read another
task's output, go to the free worker that reads the fewest of their input bytes remotely
(data-local), and if nothing is running at all any ready task is taken, so every graph
completes. So for multi-stage graphs (competing, DROID) only the first stage is planned and later
stages fall back to data-local; planning intermediates' placement is not done.

On the skew graph the LP says, in model seconds: at i1 move 12 tasks off node 0 (T = 17.6 s);
i16 14 (20.1 s); i128 24 (34.6 s); i1024 48 (78.1 s); on uniform data at most one (42.6 s at
i256). The dispatcher realises the low-intensity plans (20.2 s at i1, 39.1 s at i128 in the
forecast) but not the high-intensity one: at i1024 it moves the planned 48 and still takes
110.7 s against the LP's 78.1 s. The LP lets a worker read over the link while another task's
compute runs, but a worker here is one slot that reads, computes and writes in sequence, and a
transfer can start only when the link is free and a worker is free at that moment; at i1024 the
link must be busy 88% of T and the workers rarely line up with it. Taking link-bound tasks first
did not help (109.3 s); dropping the one-transfer-per-link gate made it worse (122.4 s).

Planning cost: building the LP 11–33 ms and solving both LPs 6–14 ms with the solver below
(HiGHS solves the same LP in 1.6–3.8 ms and gets the same T and bytes in every case; both from
`sched_lp_bound.py`, HiGHS from scipy 1.13.1 in a scratch venv); everything lp-plan spends in
`choose` over a whole run, the LP included, is 0.03–0.06 s.

The solver: scipy 1.9.3, the version in the lab containers, does not import with numpy 2.0.2
(checked in a venv with exactly those two: `ImportError: cannot import name 'Inf' from
'numpy'`), so the LP is solved by a 90-line dense two-phase simplex with Bland's rule (the
placement LP is degenerate: every resource row has a zero right-hand side). It is tested
against brute-force vertex enumeration on 300 random small LPs. scipy is not installed in the
VM's `.venv` and not added to the requirements.

Implementation: `cephdag/policies/lp.py`: classes `task_class` line 15, the LP `PlacementLp`
24 (coefficients 45–79, the two solves 81–101, rounding 105), the dispatcher `LpPlan` 117
(`choose` 138, the lag rule `_planned` 180, the link gate `_link_busy` 204);
`cephdag/policies/simplex.py` line 14. All three are registered in
`cephdag/policies/__init__.py` lines 24–27 and usable by name in `run.py` and campaign specs.

## In-process evaluation

`demo3/sched_forecast.py` runs each policy to completion in two simulations of the cephdag8 cs
cluster with the calibrated models: `model.Simulation` ("equal"), the forecast cephdag itself
uses (greedy-wait and beam plan with it), which gives each concurrent stage an equal share of
every resource it uses; and `FairSimulation` ("fair"), which shares the same capacities
max-min fairly. They differ exactly where this problem lives: under equal sharing a 47 MB/s
transfer out of node 0 takes half of node 0's 264 MB/s disk, so node 0's own reads halve while
anything is shipped off it. Policies that forecast internally (greedy-wait, stealing) still get
the equal model inside the fair run, as in the lab. Data: `demo3/sched-forecast/` (forecast.json,
lp_bound.jsonl, tables.md from `sched_table.py`).

Fair-sharing forecast: wall, bytes moved; in brackets the measured median of 3 repeats (skew
from demo3-skew, uniform from demo3-sweep, which ran only data-local and heft in cs mode).

| policy | skew i1 | skew i16 | skew i128 | skew i1024 | uniform i256 | uniform i1024 |
|---|---|---|---|---|---|---|
| heft-cc | 20.2 s, 2.35 GB | 22.8 s, 2.62 GB | **37.7 s**, 3.02 GB | **93.8 s**, 4.50 GB | 43.7 s, 0 | 143.0 s, 0 |
| backlog | 20.2 s, 0.81 GB | 22.8 s, 0.87 GB | 38.9 s, 1.48 GB | 113.5 s, 2.68 GB | 43.7 s, 0 | 143.0 s, 0 |
| lp-plan | 20.2 s, 0.81 GB | **22.6 s**, 0.94 GB | 39.1 s, 1.61 GB | 110.7 s, 3.22 GB | 44.1 s, 0.07 GB | 143.0 s, 0 |
| data-local | 22.7 s, 1.28 (22.0 s, 1.01) | 23.7 s, 1.28 (22.2 s, 1.28) | 40.4 s, 1.54 (37.7 s, 1.61) | 101.8 s, 2.89 (97.0 s, 2.89) | 44.1 s, 0.13 (47.9 s, 0.07) | 143.0 s, 0 (142.6 s, 0) |
| greedy-wait | 21.8 s, 0 (19.1 s, 0) | 25.7 s, 0 (23.5 s, 0) | 55.1 s, 0 (51.4 s, 0) | 103.0 s, 3.76 (102.0 s, 3.96) | 43.8 s, 0 | 143.0 s, 0 |
| stealing | 22.4 s, 0.94 (21.4 s, 0.87) | 23.0 s, 0.94 (21.9 s, 0.87) | 40.3 s, 1.54 (37.6 s, 1.41) | 113.5 s, 2.68 (104.5 s, 2.82) | 43.7 s, 0 | 143.0 s, 0 |
| heft | 45.2 s, 2.68 (45.2 s, 2.68) | 38.8 s, 3.83 (39.4 s, 3.83) | 55.7 s, 3.89 (56.1 s, 3.89) | 110.6 s, 4.43 (110.5 s, 4.43) | 55.4 s, 5.17 (60.3 s, 5.17) | 161.7 s, 8.05 (164.1 s, 8.05) |
| beam-4 | 31.3 s, 2.28 (33.4 s, 2.21) | 32.5 s, 2.21 (31.6 s, 2.01) | 39.2 s, 1.41 (37.6 s, 1.68) | 103.7 s, 3.02 (101.2 s, 2.95) | 43.7 s, 0 | 143.0 s, 0 |
| LP bound | 17.6 s | 20.1 s | 34.6 s | 78.1 s | 42.6 s | 141.8 s |

Where the model and reality agree and disagree:

- With fair sharing the forecast is within −8% to +14% of all 24 measured cells, gets the bytes
  moved within 0.27 GB, reproduces the measured order of the five skew policies at i1 and i16,
  and at i128 the near tie of beam-4, stealing and data-local (37.6–37.7 s measured, 39.2–40.4 s
  forecast) ahead of greedy-wait and HEFT. At i1024 it puts stealing last, 113.5 s against a
  measured 104.5 s (+9%).
  HEFT's walls and bytes are reproduced almost exactly at every skew intensity, so HEFT's loss
  is explained by its plan, not by noise.
- With equal sharing, cephdag's own forecast, the movers are wrong by up to 2.4x: data-local at
  i1 53.6 s against a measured 22.0 s, stealing 52.3 s against 21.4 s, beam-4 53.6 s against
  33.4 s. So beam's 2.2 GB at i1 were chosen with a forecast that misprices precisely the moves
  out of node 0. For greedy-wait, which does not move, and for HEFT the two models agree within
  3%. I did not change `model.Simulation`: the equivalence tests pin it to
  the Rust crate; it is the next thing to fix if the lab confirms the fair forecast.
- The largest remaining error is node 0's local work: the model says 0.335 s per task, the lab
  0.29 s (greedy-wait +14% at i1). Per-task overhead and write costs are not what is missing:
  the model has no fixed per-task cost and still overestimates.
- The uniform i256 forecasts are 8% optimistic for both measured policies (data-local's three
  repeats spread 43.2–49.5 s).
- In the fair forecast heft-cc is best or tied at every skew intensity (37.7 s at i128 against
  beam's 39.2 s; 93.8 s at i1024 against data-local's 101.8 s), but 2.35–4.50 GB of its bytes
  are moves among lightly loaded nodes. Backlog and lp-plan tie it at i1 and i16 with a third of
  the bytes and lose at i1024 for the reasons above. The gap to the LP bound at i1024 (78.1 s
  against 93.8 s at best) is the single-slot worker that cannot overlap a transfer with compute.

## At i1, how many tasks should leave node 0, and can anything beat greedy-wait's 19.1 s?

From the measured rates: node 0 needs a = 0.29 s per task locally, a shard takes d = 1.4 s on
its link, and moves out of node 0 run one after another. Moving m tasks, node 0 finishes after
(65 − m) · a and the last moved task after m · d + c, with c ≈ 0.2 s for its compute and write
on the receiving node. These are equal at m = (65a − c)/(a + d) = 18.65/1.69 ≈ 11, giving
54 × 0.29 = 15.7 s, plus the 0.25 s greedy-wait's 19.1 s spends beyond 65 × 0.29 s: about
16 s, 3 s (16%) faster. That assumes a transfer does not slow node 0's own tasks. If each
active transfer took 18% of node 0's throughput (the model's share of node 0's disk that a
47 MB/s read uses), node 0 needs (65 − m) · 0.29 + 0.18 · 1.4 · m, the balance moves to m ≈ 13
and the wall to about 18.6 s. The fair forecast sits between: all three new policies move 12
tasks (0.81 GB for backlog and lp-plan) and forecast 20.2 s against greedy-wait's 21.8 s, 7%
less, which scaled to the measured 19.1 s is about 17.7 s.

So: move about 11–13 shards (0.7–0.9 GB), one at a time over node 0's link, and the predicted
wall is 16–18.6 s, most likely near 17.7 s; yes, a policy can beat 19.1 s, by 0.5–3 s. The same
arithmetic explains why stealing and data-local lose there: they pull from node 0 seven at a
time, the seven share the link and all arrive after 7 × 1.4 = 9.8 s, so the second batch lands
around 21 s (measured 21.4 and 22.0 s). The margin is small against run-to-run spread (at i1
the three repeats of data-local spread 1.3 s, stealing's 2.5 s, greedy-wait's 0.1 s), so it
needs the 3 repeats of sched-skew;
sched-pilot's one repeat can only show whether the new policies move about 12 tasks and land
near 17–18 s.

## Lab preparation (not launched)

Campaign specs on branch `schedulers`, cs mode only (the driver needs no disagg mode for them),
each with its own dataset deleted at the end, built from demo3-skew.json and demo3-sweep.json
(same models, shard size, placement and workload; a test checks this):

| spec | what | estimate |
|---|---|---|
| `experiments/campaigns/sched-pilot.json` | skew 8 GiB, i1 and i128, 1 repeat; heft-cc, backlog, lp-plan, data-local, greedy-wait, heft | 0.34 h (plan) / 0.37 h (chain_estimate) |
| `experiments/campaigns/sched-skew.json` | skew 8 GiB, i1/16/128/1024, 3 repeats; heft-cc, backlog, lp-plan, data-local, greedy-wait, stealing, heft, beam-4 | 2.30 h / 2.75 h |
| `experiments/campaigns/sched-uniform.json` | uniform 16 GiB 64 MiB round-robin, i256 and i1024, 2 repeats; heft-cc, backlog, lp-plan, data-local, heft | 0.96 h / 1.18 h |

The chain estimate prices the new policies at HEFT's measured rate, the slowest cs policy at
every one of these points, so it is an upper bound if they are no slower than HEFT. With
demo3-overlap-sweep (3.41 h) and demo3-scale (3.9 h, PLAN.md) stage B is about 11.6 h.

Stage B: `demo3/demo3-stage-b.sh`, copied to `/mnt/mass/cephdag/tools/demo3-stage-b.sh` on
aakashpc and not started. It pins cephdag `cbd1561` (branch `schedulers`), which is in
`~/experiments/cephdag-demo3-next` as branch `schedulers`, fetched there from the bundle
`/mnt/mass/cephdag/tools/cephdag-schedulers-cbd1561.bundle` (sha256 `f8d2dcc4…`); the -next
repository's `demo3` branch and checkout are unchanged. It waits, with a heartbeat every 5 min (the shared `demo3-wait-chain.sh`), for the newest
`~/runs/demo3-stage-a-*` to exit 0 with "stage A complete" in its log and none of its processes
left; on a non-zero exit, a lost run, or no finish by Sun 2026-11-15 06:00 EST it exits 1
without touching the lab. Then: free space and Ceph state, the clone
`~/experiments/cephdag-demo3` checked clean, branch `schedulers` fetched from
`~/experiments/cephdag-demo3-next` and checked out (it must descend from the clone's commit),
`deploy_code.py` into all 17 containers with the tree-hash check and the commit in
`deployed-commit.json`, the derived models against SHA256SUMS, `lab.py verify cs`, then
sched-pilot, sched-skew, sched-uniform, demo3-overlap-sweep, demo3-scale with timestamped
start/complete lines (a failed sched-pilot skips sched-skew and sched-uniform, which run the same
new policies for hours; any other failure is reported and the next campaign runs), and
`mode cs --functions cephdag.droid`. `demo3/test_stage_b.sh` (also in the tools directory)
exercises the gate against fake run directories under `/mnt/mass/cephdag/tmp`: 10 checks in 9
cases pass on aakashpc (no run directory, exit 0, exit 1, lost, lingering process, deadline,
exit 0 without the completion line, the newer of two runs deciding either way, and the live
stage A keeping it waiting). The clone steps (fetch, ancestry check, checkout of `schedulers`)
were run in a scratch clone of -next under `/mnt/mass/cephdag/tmp` and moved it from `bb7de4b`
to `cbd1561`. `sh -n` and shellcheck pass. Nothing after the gate has run.

## Not verified

- Nothing here has run on Ceph. The forecasts are model numbers; the fair-sharing simulator is
  a harness in `demo3/`, not part of cephdag.
- scipy 1.9.3 failing with numpy 2.0.2 was checked in a VM venv, not inside a container.
- lp-plan on multi-stage graphs plans only the first stage; its competing and DROID runs are
  tested for completion and correct outputs, not for speed.
