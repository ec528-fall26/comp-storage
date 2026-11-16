# Gumbel tuning plan, fixed before any tuning run

Written 2026-11-14 15:00 ET, before the first tuning run, and committed so that its time shows it.
Code: cephdag branch `gumbel` at be0300b (worktree ~/projects/comp-storage/worktrees/gumbel).
Driver: demo3/gumbel_eval.py; models: the cephdag8 calibration copied to
~/.hermes/cache/scratch/sched/models.json. World: demo3/sched_forecast.py's FairSimulation.

## Budget

Each search gets the same number of configurations, 16, all at n = 32 simulations (the review's
criterion), each run with seeds 1 and 2 on the four tuning cases below, planning free (not charged
to the wall) and no time cut (budget_ms 1e9) so that a run depends only on its seed. That is 128
runs per search, 256 in all. Every configuration's result is reported, not only the best.

Fixed for both: margin 0.5 (search only contentious decisions), horizon 12 dispatches per rollout,
fair forecast, objective as the other native policies (seconds, bytes as tie-break).

- gumbel-32, 16 configurations: k sampled root actions in {8, 16} x selection below the root in
  {UCT (MCTS's tree), Gumbel's deterministic non-root rule} x leaf value in {tail estimate, custom
  per-node bound} x prior temperature in {1, 0.1}.
- restarts-32, 16 configurations: leaf value in {tail, bound} x prior temperature in
  {0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8}. (Restarts have no k or tree, so temperature, the knob that
  decides how far from the greedy action their first actions stray, gets the grid.)

## Tuning cases (not used in the evaluation)

hot5-i1, hot5-i64, hot5-i1024 (4 GiB of 64 MiB shards, 5 of every 11 on node 0, independent
tasks, ratio 0.25) and uniform8-i256 (8 GiB round-robin). The evaluation cases are the 8 GiB skew
at i1/16/128/1024 and 16 GiB uniform at i256/1024.

## Selection rule

For each configuration, the mean over the four cases of (wall / data-local's wall on that case),
averaged over the two seeds. The lowest wins; a configuration within 0.5% of the lowest that runs
fewer simulated transitions wins instead. The chosen configuration of each search is then
evaluated at n = 16, 32 and 64 with seeds 1, 2, 3 on the evaluation cases, planning time charged
to the wall and the 40 ms per-decision cut that beam-4 and native MCTS use in the lab.
