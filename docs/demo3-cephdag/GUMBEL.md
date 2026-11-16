# Gumbel MCTS for the cephdag scheduler: what was built, why, and what it shows

Written 2026-11-14 by Jarvis for Aakash, who asked Jarvis to build it ("You should", 2026-11-14).
Code: cephdag branch `gumbel` from `lp-plan2` (worktree
`~/projects/comp-storage/worktrees/gumbel`), commits `0dfc86e` (fair forecast), `d584d5e` (test
that the fair flag reaches beam), `be0300b` (Gumbel search and the restart control), `9348299`
(options override what a name implies), `0359f81` (tuned temperatures), `925abab` (lab spec).
File:line references are to `925abab`. Nothing here has run on the lab. Every
number below is an in-process forecast: the world is `demo3/sched_forecast.py`'s FairSimulation
with the cephdag8 calibration, which is also, by construction, the planner's own model.

## 1. The problem

The coordinator makes one decision at a time: which ready task to start on which free worker,
and where to store each of its outputs, or to wait. On the skewed 8 GiB graph that is 65 of 128
64 MiB shards on node 0 and 9 on each other node, every node a storage node with one worker
behind a 400 Mbit link (about 47 MB/s), against local reads at 260-390 MB/s. The interesting
decisions are the ones where a worker on nodes 1-7 runs out of local work while node 0 still has
a backlog: moving a shard costs about 1.4 s of node 0's send link, running it locally costs a
slot on node 0 later. Planning time is part of the wall time, because the coordinator plans
serially between dispatches.

The existing native MCTS (`native-mcts-heuristic-128`, `native/src/lib.rs:1159`) treats each
decision as a game-tree search. Its root has up to 24 candidates, the best by a one-step
profile, plus the wait (`actions`, lib.rs:982); it runs UCT with 128 iterations, each a rollout
of 12 dispatches by a heuristic default policy (best one-step profile among the first four ready
tasks, a random dispatch 20% of the time, lib.rs:1055) ending in the tail estimate
`time + (unassigned work + running work) / nodes` (lib.rs:648).

## 2. Why UCT with about 128 simulations over about 25 root actions is the wrong tool

UCT comes from the bandit algorithm UCB1, which minimises cumulative regret: the loss summed
over every pull. That is the right objective when each simulation is itself a decision that
pays, and it is why UCB keeps returning to the action that looks best. At the root of a planner
only the final recommendation is executed, so the right objective is simple regret, the loss of
the one action finally chosen; the Gumbel paper states this at `literature/sources/gumbel.txt`
lines 138-142, citing Bubeck et al. (2011), Hay and Russell (2011), and Tolpin and Shimony (2012).
For simple regret with a fixed budget, Sequential Halving (Karnin, Koren and Somekh, 2013) spends
the budget evenly on all candidates, discards the worse half, and repeats, so no simulation is
spent exploiting an action that is already known to be good.

With 128 simulations and 25 root actions, UCT expands every root action once (25 simulations) and
then spends the rest by UCB, so each action has about five noisy rollouts. In the lab the
40 ms per-decision cut takes away most of even that: in the forecasts below, native MCTS is cut
before its 128th iteration in 236-376 of 507-524 decisions on the skew graph and in 669-775 of
986-1,053 on the uniform graph (both variants, seeds 1-3). The hard top-24 cut is a second problem: an action ranked 25th
by one-step profile is never considered, however good its long-term effect.

## 3. What Gumbel-Top-k with Sequential Halving does

Danihelka et al. (ICLR 2022) give a root search with three parts.

The first is sampling without replacement. Give every root action a prior logit, add independent
Gumbel(0) noise g to each, and keep the k actions with the largest `g + logit`. This Gumbel-Top-k
trick draws k actions without replacement from softmax(logits); the single largest is an exact
sample from the prior. No action is excluded in advance: a low-prior action is merely less likely
to be drawn.

The second is Sequential Halving over the k sampled actions. With n simulations it runs
ceil(log2 k) phases; in each phase every remaining action gets the same number of simulations
(about n / (ceil(log2 k) * remaining)), then the half with the highest `g + logit + sigma(q)`
stays. Here q is the action's mean value so far, and sigma is the monotone transform
`(c_visit + max visits) * c_scale * q_normalised`, with q normalised to [0, 1] across the
actions (c_visit = 50, c_scale = 0.1, the paper's Atari setting, gumbel.txt lines 599-603, and
mctx's default; Go used c_scale = 1).

The third is the final choice, the action with the highest `g + logit + sigma(q)` among those
that reached the last phase. The paper's guarantee (gumbel.txt lines 150-158 and 211-218): with
exact Q-values, for any fixed g, the chosen action is at least as good as `argmax(g + logit)`
among the sampled ones, because sigma is monotone and adding it can only move the argmax to an
action with higher q. Since `argmax(g + logit)` is a sample from the prior, the search is a
policy improvement over the prior in expectation, for any number of simulations, even n = 2.
Two premises carry that guarantee, and both matter below: the improvement is over a *sample* from
the prior, not over the prior's best action; and it assumes exact Q-values. With stochastic
rollouts the paper only says Sequential Halving is the bandit that was easiest to tune
(gumbel.txt lines 222-235).

Below the root the paper uses a deterministic rule: visit the child whose share of visits falls
furthest below the improved policy softmax(logits + sigma(completed Q)) (its equation 14).

### Worked example: k = 4, n = 8

Four sampled actions A, B, C, D have `g + logit` = 1.2, 0.2, -0.1, 0.6; A is the prior's draw.
ceil(log2 4) = 2 phases. Phase 1 gives each action 8 / (2 * 4) = 1 simulation; the rollouts
forecast makespans A 30 s, B 26 s, C 29 s, D 27 s. With q = -makespan, q ranges from -30 to -26,
so the normalised q are A 0, B 1, C 0.25, D 0.75. The most visits so far is 1, so the scale is
(50 + 1) * 0.1 = 5.1 and sigma is A 0, B 5.1, C 1.28, D 3.83. The scores `g + logit + sigma` are
A 1.2, B 5.3, C 1.18, D 4.43: B and D stay; A, the prior's favourite, is dropped after one bad
rollout. Phase 2 gives the remaining 4 simulations to B and D, two each: B's rollouts are 28 s and
27 s (mean of three 27.0 s), D's 25 s and 26 s (mean 26.0 s). Completed q over all four is now
A -30, B -27, C -29, D -26, normalised 0, 0.75, 0.25, 1; the scale is (50 + 3) * 0.1 = 5.3, so
B scores 0.2 + 3.98 = 4.18 and D 0.6 + 5.3 = 5.9. D is taken. (Inside phase 2 the order is B,
D, D, B: each simulation goes to the best-scoring action among those with the fewest visits the
schedule allows.) Two things to notice: sigma sees q only after min-max normalisation across the
sampled actions, so had all four means been within 0.1 s of each other, the best would still get
5.3 logits more than the worst; and with sigma's scale of about 5 against logit gaps of about 1, a
single noisy rollout can decide which half survives. The test `SequentialHalvingTests` (tests/test_gumbel.py:77)
checks this schedule on a hand-made cost table: costs 4, 1, 3, 2 give the visit order
0, 1, 2, 3, 1, 3, 1, 3 and choose action 1.

## 4. How it is built here

**Fair forecast (stage 1).** Concurrent stages now share each disk and link max-min fairly in the
Rust simulator when a graph is built with `fair` (lib.rs:118-119; progressive filling in
`fair_rates`, lib.rs:610), with `model.FairSimulation` (cephdag/model.py:653, `fair_rates` at
:668) as the Python reference, ported from `demo3/sched_forecast.py`. The old names keep equal
sharing, and the equivalence and wire fixtures pass unchanged. The new names `beam-fair-<w>` and
`native-mcts-fair-<n>` (cephdag/policies/__init__.py:54-68) are the existing algorithms with the
fair forecast, so the model fix can be measured alone. The tests in tests/test_fair_forecast.py
replay recorded data-local and heft-cc decisions on the skew graphs at i1 and i1024 and the
uniform graph at i256 and require the Rust and Python forecasts to agree within 1e-6 relative;
a hand-solved state (tests/test_fair_forecast.py:176) shows beam-4 waiting (equal sharing
forecasts 3.2 s for moving against 2.6 s for waiting) and beam-fair-4 moving (fair: 2.4 s).

**Prior.** The root actions are every candidate the existing one-step profile ranks (`ranked`,
lib.rs:965: the first eight ready tasks on every free worker with every output placement),
without the top-24 cut, plus the wait when a task is running. A candidate's logit is
`-(seconds / best seconds - 1) / T` from its one-step seconds; the wait gets the best dispatch's
logit (`Prior::new`, native/src/gumbel.rs:20). T is 0.1 for Gumbel, chosen on held-out cases
(section 5), so a candidate 10% slower than the best one-step option has logit -1.

**Root search.** `Search::gumbel` (gumbel.rs:315) draws k = 16 actions by Gumbel-Top-k (`top_k`,
gumbel.rs:49; k chosen from {8, 16} on held-out cases), runs Sequential Halving (`Halving::run`,
gumbel.rs:174, schedule in `halving_schedule`, gumbel.rs:70; sigma in `sigma_completed_q`,
gumbel.rs:94), and acts on the most visited action with the highest `g + logit + sigma(completed
Q)` (`Halving::chosen`, gumbel.rs:193). An action not yet visited gets the prior-weighted mean Q
of the visited ones, as in the paper's completed Q.

**Below the root.** The default keeps the existing UCT tree (`Search::simulate`, lib.rs:1101,
refactored out of `choose` without changing it): it was the simpler change and keeps the
comparison with native MCTS about the root. Gumbel's deterministic non-root rule is there too
(`improved_choice`, gumbel.rs:123, `simulate_improved`, gumbel.rs:234, name `gumbel-g-<n>`); in
tuning it did no better than UCT below the root.

**Leaf value.** Rollouts are MCTS's (12 dispatches, then a leaf value). `gumbel-<n>` ends in the
tail estimate; `gumbel-lb-<n>` in a custom lower bound (`State::bound`, lib.rs:664): the later of
the tail estimate and, per node, a fluid relaxation of its own ready tasks, in which a fraction f
of their local seconds L leaves over the node's send link (f * B link seconds after the l seconds
already queued on it) and the rest runs after the r seconds left of its running task; the node
needs min over f of max((1 - f) * L + r, l + f * B). Tuning picked the bound.

**Contentious decisions only.** `plain` (gumbel.rs:201) takes the best-ranked dispatch without
search when it moves no bytes and every dispatch that moves bytes needs more than 1.5 times its
one-step seconds (margin 0.5). Search then ran on 45-186 of about 520 decisions on the skew graph
and 280-390 of about 1,040 on the uniform graph (table in section 6). At high intensity the rule
lets more through, because compute dominates a remote dispatch's one-step time.

**Restart control.** `restarts-<n>` (`Search::restarts`, gumbel.rs:367) is the equal-budget
control: n times, sample a first action from the same one-step profile (with its own
temperature, 0.25, tuned with the same budget as Gumbel's), roll out with the same default policy
over the same horizon to the same leaf value, and execute the first action of the cheapest. It has no tree and no
halving, so a Gumbel win over it would be a win for the search, not for the prior, the forecast
or the budget. Its rollouts cannot wait after the first action (the default policy never leaves a
worker idle), which `test_restarts_cannot_wait_past_the_first_action` (tests/test_gumbel.py:218)
records. Its budget is matched in simulations, not exactly in transitions: per run it simulated
0.99-1.20 times Gumbel's transitions at n = 16, 1.10-1.53 times at n = 32 and 1.14-1.48 times at
n = 64 (section 6), because its rollouts start from the root rather than from a tree node.
restarts-16 simulates fewer transitions than gumbel-lb-32 in every case and is reported too.

**Reproducibility.** The searches draw from a Mersenne Twister whose state crosses the ABI with
each decision, seeded per policy (`seed`, default 1); the same seed gives the same decisions
(tests/test_gumbel.py:159). Python side: `RootSearch`, `Gumbel`, `Restarts` in
cephdag/policies/native.py:331, :425, :435. The ABI is now 7.

**Tests.** tests/test_gumbel.py: Gumbel-Top-k samples without replacement, is reproducible by
seed, and its first draw follows softmax(logits) (4,000 draws within 0.03); Sequential Halving
keeps the right halves on hand-made costs with 4 and 8 actions, and lets a large prior beat a
small Q advantage but not a large one; the plain rule skips search; Gumbel spends exactly n
simulations on k sampled actions; outputs go where the forecast says; the searches finish random
DAGs on 3 and 6 nodes and a verified cs run whose output digest equals fifo-hash's. Full suite:
286 tests, OK, 0 skipped (was 263 at ac2f85d).

## 5. Tuning, fixed before it ran

`demo3/gumbel-tuning-plan.md` was committed (projects repo `344d014`) before any tuning run: 16
configurations each for gumbel-32 and restarts-32, seeds 1 and 2, four held-out cases not used
in the evaluation (4 GiB with 5 of every 11 shards on node 0 at i1, i64, i1024; 8 GiB uniform at
i256), score = mean wall / data-local's wall, planning free and uncut. Every configuration tried
is listed in the appendix. Chosen: Gumbel with k = 16, UCT below the root, bound leaf, T = 0.1
(score 0.986); restarts with tail leaf, T = 0.25 (score 0.968). The restarts' best configuration
was already better than Gumbel's best on the held-out cases. The temperature mattered most for
both: Gumbel scored 0.99-1.05 at T = 0.1 and 1.15-1.24 at T = 1, i.e. the searches did best
with a prior close to the greedy one-step choice.

## 6. In-process results

Cases: the 8 GiB skew at i1, i16, i128, i1024 and 16 GiB uniform (round-robin) at i256 and i1024,
independent tasks, ratio 0.25. Planning time is measured on JarvisVM and charged to the wall
before each dispatch; searches use seeds 1-3 (mean, range), the 40 ms per-decision cut that beam
and MCTS use in the lab, and the tuned settings. Driver: `demo3/gumbel_evaluate.py`; rows
`demo3/gumbel-eval/evaluation.jsonl`; all tables, including planning-free walls, in
`demo3/gumbel-eval/tables.md`.

Wall in seconds, planning charged:

| policy | skew-i1 | skew-i16 | skew-i128 | skew-i1024 | uniform-i256 | uniform-i1024 |
|---|---|---|---|---|---|---|
| heft-cc | 20.3 | 22.9 | 37.8 | 93.9 | 43.8 | 143.2 |
| data-local | 22.7 | 23.7 | 40.4 | 101.8 | 44.1 | 143.0 |
| beam-4 | 31.3 | 32.6 | 39.3 | 102.6 | 43.8 | 143.1 |
| beam-fair-4 | 31.3 | 32.6 | 39.3 | 101.8 | 43.8 | 143.1 |
| native-mcts-heuristic-128 | 24.9 (22.4-26.2) | 30.0 (27.7-33.6) | 50.3 (44.6-55.0) | 95.5 (92.1-97.7) | 53.2 (51.9-53.9) | 154.3 (152.9-155.1) |
| native-mcts-fair-128 | 24.7 (23.5-25.4) | 26.2 (25.4-27.3) | 39.3 (38.6-40.2) | 94.4 (90.8-97.3) | 54.0 (53.5-54.4) | 154.5 (154.3-154.9) |
| gumbel-lb-16 | 26.3 (25.4-27.2) | 27.5 (25.6-29.6) | 40.6 (38.2-42.8) | 97.6 (95.2-100.0) | 50.0 (49.6-50.2) | 161.2 (158.5-163.2) |
| gumbel-lb-32 | 24.3 (22.5-25.4) | 25.8 (25.1-26.8) | 40.5 (39.2-42.9) | 97.7 (96.2-98.5) | 48.5 (47.3-49.7) | 158.4 (157.5-159.5) |
| gumbel-lb-64 | 26.6 (25.9-27.1) | 28.3 (27.5-29.0) | 38.3 (37.8-38.6) | 94.7 (93.8-96.1) | 49.3 (48.6-49.7) | 156.4 (154.9-158.4) |
| restarts-16 | 23.4 (22.4-24.1) | 25.9 (25.5-26.7) | 42.1 (41.5-42.8) | 93.9 (91.4-95.4) | 49.8 (49.4-50.5) | 160.9 (159.1-162.4) |
| restarts-32 | 23.2 (22.3-23.8) | 26.2 (23.4-27.9) | 43.7 (42.9-45.3) | 93.6 (93.1-94.6) | 47.9 (46.4-49.0) | 153.7 (153.4-154.1) |
| restarts-64 | 21.9 (21.5-22.1) | 24.9 (23.8-25.7) | 48.2 (44.1-53.3) | 93.8 (93.1-94.9) | 46.6 (45.4-47.3) | 151.3 (150.4-152.3) |

Planning seconds per run and GB moved (charged runs; searches mean of 3 seeds):

| policy | skew-i1 | skew-i128 | skew-i1024 | uniform-i256 | uniform-i1024 |
|---|---|---|---|---|---|
| heft-cc | 0.10 s, 2.35 GB | 0.11 s, 3.02 GB | 0.11 s, 4.50 GB | 0.15 s, 0.00 GB | 0.15 s, 0.00 GB |
| data-local | 0.02 s, 1.28 GB | 0.02 s, 1.54 GB | 0.02 s, 2.89 GB | 0.05 s, 0.13 GB | 0.05 s, 0.00 GB |
| beam-4 | 0.75 s, 2.28 GB | 0.54 s, 1.41 GB | 0.70 s, 3.09 GB | 0.60 s, 0.00 GB | 0.55 s, 0.00 GB |
| native-mcts-heuristic-128 | 0.96 s, 3.99 GB | 1.17 s, 4.83 GB | 0.80 s, 5.69 GB | 1.46 s, 3.33 GB | 1.44 s, 3.47 GB |
| native-mcts-fair-128 | 1.83 s, 3.79 GB | 1.81 s, 4.95 GB | 2.66 s, 5.61 GB | 4.53 s, 3.50 GB | 4.33 s, 3.77 GB |
| gumbel-lb-32 | 0.14 s, 2.99 GB | 0.54 s, 3.88 GB | 0.55 s, 6.27 GB | 1.35 s, 1.67 GB | 1.15 s, 5.47 GB |
| restarts-32 | 0.15 s, 3.06 GB | 0.59 s, 4.29 GB | 0.61 s, 6.22 GB | 1.44 s, 1.29 GB | 1.11 s, 3.63 GB |

Tasks run off node 0 with an input there (skew i1/i16/i128/i1024): heft-cc 12/13/23/45,
data-local 14/15/23/43, beam-4 20/20/21/43, gumbel-lb-32 16/16/26/46, restarts-32 16/18/25/46.
Gumbel searched 45, 47, 159, 140 of about 515-528 decisions on the skew graph and 329 and 280 of
about 1,030-1,057 on the uniform graph. Simulated transitions per run, gumbel-lb-32 against
restarts-32: 67k/82k (skew-i1), 243k/372k (i128), 215k/259k (i1024), 492k/599k (uniform-i256).

**The review's success criterion is not met.** It asked for Gumbel-SH at n = 32 to beat
UCT-MCTS-128 and the restart control by at least 3% at no more than beam-4's planning time.
Against native-mcts-heuristic-128, gumbel-lb-32 is 2.2%, 14.3%, 19.5% faster on skew i1, i16,
i128, 2.3% slower at i1024, 8.8% faster on uniform-i256 and 2.6% slower at i1024. Against
restarts-32 it is 4.9% slower, 1.7% faster, 7.2% faster, 4.3% slower, 1.2% slower, 3.0% slower:
it clears 3% in one case of six. Its planning time is at or below beam-4's on the skew graph
(0.14-0.55 s against 0.54-0.75 s) and about twice it on the uniform graph (1.15-1.35 s against
0.55-0.60 s). And no search beats heft-cc anywhere by more than 0.3% (restarts at skew-i1024,
within noise); gumbel-lb-32 loses to it by 4-20%. On the uniform graph, where heft-cc and
data-local move nothing and any move is a loss, the Gumbel and restart searches move 0.5-6.6 GB
and lose 5.7-14%.

**The model fix alone.** beam-fair-4 equals beam-4 to within 0.05% in every case with planning
free, and to within 0.9% with planning charged (the 0.8% at skew-i1024 is timing: free, the two
differ by 0.01 s). This is real, not a dead flag: the hand-solved state above makes them differ,
and on beam-4's own trajectory in the fair world, beam-fair-4 picks a different action at 1 of 231
decisions at i1, 0 of 200 at i128 and 7 of 245 at i1024 (`demo3/gumbel-eval/beam_fair_decisions.py`
and `.txt`). The reason is in how beam scores a state: it ranks states after each level by the
tail estimate, which prices running stages at their isolated rates (remaining / slowest
resource, lib.rs:648-658) and spreads the rest evenly over the nodes, and most levels are
dispatches, which do not advance time; the sharing rule only acts through the few `advance`
steps inside a 12-dispatch window. native-mcts-fair-128 against native-mcts-heuristic-128, whose
rollouts advance time at every busy step, is different: 0.7%, 12.7% and 22.0% faster on skew
i1, i16, i128 (planning charged; 10.4%, 13.8%, 18.9% with planning free), 1.1% at i1024, and
0.2-1.4% slower on uniform, at 1.5-3.3 times the planning time (fair rates cost more to
compute). So fixing the forecast is most of what any search here gains over the old MCTS: on skew
i16 and i128, where gumbel-lb-32 led native-mcts-heuristic-128 by 14.3% and 19.5%, it is 1.8%
ahead of native-mcts-fair-128 at i16 and 3.2% behind it at i128.

**DROID batch planning cost** (`demo3/gumbel_droid_cost.py`, rows `demo3/gumbel-eval/droid-cost.json`;
a synthetic batch shaped like demo3-droid's batch 2: 2,255 partitions placed at random on the 8
nodes, 9,020 tasks, 69.1 GB of source, built by `experiments.droid_workload.build_graph`;
planning measured but not charged; the lab's 40 ms cut applies). beam-4 planned 227 s
in total over 38,769 choose calls (median 6.6 ms, p90 9.8 ms, max 40.5 ms), forecast wall
824 s, 15.6 GB moved. gumbel-lb-32 planned 68.7 s (median 0.005 ms, because most calls find no
free worker or a plain decision; p90 6.5 ms, max 40.6 ms; 11,174 searches, about 6.1 ms each),
forecast wall 893 s, and moved 81.1 GB. data-local: 2.0 s, 655 s, 7.1 GB. (In the lab beam-4
planned 80 s on batch 2; JarvisVM's in-process figure includes Python-side state encoding and is
not comparable in absolute terms.) On DROID, Gumbel plans for a third of beam-4's time but moves
11 times data-local's bytes and is forecast 36% slower than data-local.

## 7. What this shows and what it does not

The world here equals the planner's model, so these numbers are an upper bound on what search
can gain: a planner whose forecast is exact should, given enough simulations, never lose to a
heuristic that ignores the forecast. It does lose, to heft-cc everywhere and to data-local on the
uniform graph and on DROID. The losses are therefore not model error. Three measurements point
at the evaluation signal, the rollouts, rather than at how the simulations are allocated:

1. The control without a tree or halving does as well as Gumbel: restarts-32 is within 8% of
   gumbel-lb-32 in every case and better on four of six, and restarts-64 is the best search
   overall (mean wall 1.09x heft-cc's, against 1.11x for gumbel-lb-32).
2. More simulations do not help Gumbel consistently: gumbel-lb-64 is better than -32 on three
   cases and worse on three.
3. Removing the leaf value makes it worse: rollouts to the end of the graph (horizon 10,000, no
   time cut, seeds 1-2, post-hoc, `demo3/gumbel-eval/diagnostic-horizon.jsonl`) gave gumbel-lb-32
   29.9, 31.9, 43.2, 102.4 s on skew i1, i16, i128, i1024, against 27.5, 25.7, 39.3, 95.8 s with
   12-dispatch rollouts and the same seeds (planning free in both); restarts-32 likewise, 28.1,
   29.3, 41.6, 102.1 s against 24.6, 26.6, 42.3, 93.0 s.

The likely mechanism, not separately measured: the default policy dispatches at random 20% of
the time, and a random dispatch on this graph usually moves a 64 MiB shard over a 47 MB/s link,
so one rollout's makespan varies by seconds, while the first actions being compared differ by
fractions of a second. sigma normalises q to [0, 1] across the sampled actions whatever the
absolute differences, so with one or two rollouts per action the noisiest lucky action gets a
bonus of about 5 logits, larger than the prior's gap between a local and a remote dispatch at
T = 0.1. (MuZero normalises by the range of values found anywhere in the tree, including the
parent's value, which damps this; the paper itself suggests normalising by the standard deviation
of an advantage estimate instead, gumbel.txt lines 599-612.) Gumbel's guarantee needs exact Q
(section 3); it also only promises to beat a *sample* from the prior, while heft-cc and data-local
are deterministic and close to the prior's best.

What the results do not show: anything about the lab, where the forecast is within -8% to +14%
of measured skew walls for these policies but has not been checked for search policies (stage E
measures native MCTS there); whether a better default policy would change the picture; whether
other values of c_visit and c_scale, or a fixed rather than min-max normalisation of q, would.
None of those was in the pre-registered grid.

## 8. What I would do next, and the lab run

By the review's rule the lab run should wait: the criterion was the gate. The spec and stage
script are ready in case Aakash wants the measurement anyway (for example to show the negative
on real hardware). If the search is pursued, the change most likely to matter is the rollout
policy, not the root: roll out with heft-cc itself, deterministically, to the end of the graph,
and pick the first action whose heft-cc continuation finishes earliest. That is Bertsekas's
rollout algorithm; in a deterministic model whose forecast equals the world it cannot do worse
than heft-cc, because heft-cc's own first action is among those compared. It is a new planner,
so it is his call, and it needs heft-cc's insertion logic in Rust or about 25 Python forecasts
per contentious decision.

## 9. Lab preparation (not launched)

- Spec `experiments/campaigns/sched-gumbel.json` (cephdag `925abab`): sched-skew's 8 GiB skew,
  i1, i128, i1024, 2 repeats, cs mode, gumbel-lb-32, restarts-32, native-mcts-fair-128,
  beam-fair-4, heft-cc, own dataset `sched-gumbel-8g-64m`, deleted at the end. Estimate 0.85 h
  (`campaign.py plan`) and 0.97 h (`demo3/chain_estimate.py`, searches priced at heft's sched-skew
  rate, beam-fair-4 at beam-4's), plus about 0.15 h for deploy, verify and the mode switch.
- Stage script `demo3/demo3-stage-f.sh`: waits for stage E to exit 0 with `stage E complete` in
  its log (start deadline Sun 2026-11-15 20:00 EST); moves the clone from stage E's `8cdc2b4` to
  `925abab` fetched from `/mnt/mass/cephdag/tools/cephdag-gumbel-925abab.bundle`; `deploy_code.py`;
  then `demo3/deploy_native.py`, because the containers hold the image's ABI 5 library
  (sha256 13e169e4..., checked read-only on cephdag8-n0) and deploy_code.py copies only cephdag/
  and experiments/: it copies `libcephdag_native-925abab.so` (sha256 8c09d583..., built on JarvisVM
  from `git archive 925abab native`; highest glibc symbol GLIBC_2.34, the image has glibc 2.34)
  into all containers, keeps the previous library, and requires every running container to load it
  with ABI 7; then the models' SHA256SUMS, a one-decision check of the four native policies in
  cephdag8-n0, `lab.py verify cs`, sched-gumbel, and `mode cs --functions cephdag.droid`.
- Checks: `sh -n` and `shellcheck` clean; `demo3/test_stage_f.sh` passes 17 of 17 on JarvisVM
  in a scratch directory (gate cases as stage E's, plus deploy_native.py against a fake docker:
  every container gets the library, a stopped container records no ABI, the image's library is
  kept, another lab's container is untouched, a wrong ABI fails).
- To launch, the parent copies `cephdag-gumbel-925abab.bundle`, `libcephdag_native-925abab.so`
  (both in `demo3/stage-f/`), `deploy_native.py`, `demo3-stage-f.sh` and
  `test_stage_f.sh` into `/mnt/mass/cephdag/tools`, reruns the gate tests there, and starts
  `runjob start demo3-stage-f`. After stage F the containers keep the ABI 7 library, which every
  later commit on this branch needs; a commit with ABI 5 would need the saved library back.

## 10. Unverified

- Nothing ran on the lab. The in-process world is the planner's model.
- The library has not been loaded in the lab image; only its glibc symbol versions were checked
  (highest GLIBC_2.34; `ldd --version` in cephdag8-n0 reports 2.34, checked read-only).
- The build of the stage F library outside the image is a departure from infra/Dockerfile, which
  builds it inside the Ceph base image.
- Planning times are JarvisVM's; aakashpc's coordinator may be faster or slower.
- The rollout-noise mechanism in section 7 is an inference from the three measurements, not a
  measurement of rollout variance.

## Appendix: every tuning configuration (demo3/gumbel-eval/tuning.txt)

Score = mean over the four held-out cases and two seeds of wall / data-local's wall (data-local:
hot5-i1 12.57 s, hot5-i64 14.92 s, hot5-i1024 49.82 s, uniform8-i256 21.85 s); transitions are
simulated transitions per run; walls are seed means.

| search | k | below | leaf | T | score | transitions | hot5-i1 | hot5-i64 | hot5-i1024 | uniform8-i256 |
|---|---|---|---|---|---|---|---|---|---|---|
| gumbel-32 | 16 | uct | bound | 0.1 | **0.9857** | 107197 | 10.74 | 15.30 | 47.55 | 24.21 |
| gumbel-32 | 8 | gumbel | bound | 0.1 | 0.9877 | 113468 | 10.84 | 15.57 | 47.69 | 23.77 |
| gumbel-32 | 8 | uct | tail | 0.1 | 0.9989 | 114034 | 10.60 | 15.67 | 47.40 | 25.14 |
| gumbel-32 | 16 | uct | tail | 0.1 | 1.0013 | 111133 | 10.62 | 15.81 | 47.81 | 24.93 |
| gumbel-32 | 16 | gumbel | bound | 0.1 | 1.0086 | 114212 | 11.26 | 15.43 | 48.32 | 24.78 |
| gumbel-32 | 16 | gumbel | tail | 0.1 | 1.0086 | 114165 | 11.15 | 15.67 | 47.84 | 24.83 |
| gumbel-32 | 8 | gumbel | tail | 0.1 | 1.0335 | 112753 | 11.64 | 16.78 | 47.61 | 24.65 |
| gumbel-32 | 8 | uct | bound | 0.1 | 1.0510 | 113338 | 12.88 | 15.70 | 47.82 | 25.51 |
| gumbel-32 | 16 | uct | tail | 1 | 1.1515 | 91963 | 11.45 | 14.53 | 50.20 | 37.43 |
| gumbel-32 | 16 | uct | bound | 1 | 1.1741 | 91721 | 12.04 | 15.29 | 49.87 | 37.43 |
| gumbel-32 | 8 | uct | tail | 1 | 1.1769 | 96311 | 11.52 | 15.29 | 50.39 | 38.35 |
| gumbel-32 | 16 | gumbel | tail | 1 | 1.1912 | 92418 | 10.93 | 16.37 | 51.62 | 38.50 |
| gumbel-32 | 8 | uct | bound | 1 | 1.2112 | 92731 | 12.21 | 15.77 | 51.58 | 38.93 |
| gumbel-32 | 8 | gumbel | bound | 1 | 1.2125 | 92102 | 12.49 | 15.12 | 51.21 | 39.66 |
| gumbel-32 | 16 | gumbel | bound | 1 | 1.2135 | 91631 | 13.00 | 14.96 | 51.08 | 39.16 |
| gumbel-32 | 8 | gumbel | tail | 1 | 1.2398 | 92085 | 13.56 | 15.68 | 50.52 | 39.65 |
| restarts-32 | | | tail | 0.25 | **0.9677** | 128054 | 10.43 | 14.78 | 48.26 | 23.65 |
| restarts-32 | | | bound | 0.25 | 0.9756 | 123144 | 10.59 | 14.83 | 47.46 | 24.33 |
| restarts-32 | | | bound | 0.1 | 0.9791 | 144486 | 11.66 | 15.10 | 46.92 | 22.61 |
| restarts-32 | | | bound | 0.5 | 1.0008 | 110037 | 10.94 | 14.59 | 47.92 | 26.07 |
| restarts-32 | | | bound | 0.05 | 1.0073 | 160068 | 11.85 | 15.30 | 49.75 | 23.22 |
| restarts-32 | | | tail | 0.5 | 1.0133 | 113132 | 11.07 | 15.44 | 47.37 | 25.95 |
| restarts-32 | | | tail | 0.1 | 1.0176 | 157462 | 11.63 | 16.82 | 46.71 | 23.62 |
| restarts-32 | | | tail | 1 | 1.0255 | 107438 | 10.43 | 14.83 | 47.60 | 28.90 |
| restarts-32 | | | bound | 1 | 1.0276 | 105010 | 10.45 | 14.75 | 48.57 | 28.74 |
| restarts-32 | | | tail | 0.05 | 1.0332 | 176620 | 11.57 | 16.81 | 50.21 | 23.55 |
| restarts-32 | | | bound | 2 | 1.0707 | 100035 | 11.21 | 15.11 | 46.88 | 31.41 |
| restarts-32 | | | tail | 2 | 1.0798 | 106218 | 10.32 | 16.19 | 47.50 | 31.90 |
| restarts-32 | | | tail | 8 | 1.1058 | 100919 | 10.98 | 14.67 | 47.17 | 35.39 |
| restarts-32 | | | bound | 4 | 1.1099 | 101340 | 10.78 | 14.92 | 48.30 | 35.23 |
| restarts-32 | | | tail | 4 | 1.1168 | 105406 | 10.62 | 15.62 | 48.46 | 35.03 |
| restarts-32 | | | bound | 8 | 1.1343 | 96892 | 11.37 | 15.10 | 47.91 | 36.25 |
