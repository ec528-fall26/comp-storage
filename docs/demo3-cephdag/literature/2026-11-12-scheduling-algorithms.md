# Scheduling algorithms that could beat beam-4, HEFT, stealing and greedy-wait on the cephdag lab

Literature review and headroom analysis, 2026-11-12. This extends the earlier reviews in `~/.hermes/experiments/computational-storage-research/` (RESEARCH-ROADMAP.md, scheduling/REPORT.md, adaptive/NOTES.md) and does not repeat them. No scheduler code was written or changed, and no lab machine was touched. The headroom analysis is a read-only pass over the 2026-11-03 baseline run files.

## Conclusion

Deeper lookahead is not where the remaining time is, and the literature agrees with the lab's numbers on that point. I computed a fluid lower bound from the baseline runs. It assumes perfect fractional placement and uses each node's median measured stage times. The bound is 40.9 s for the 32 GiB independent workload, where the best measured policy is beam-4 at 58.54 s, and 40.9 s for the 8 GiB competing workload, where the best is HEFT at 74.80 s. The headroom is therefore real, roughly 18 s and 34 s. Almost none of it comes from choosing better among the actions beam already evaluates. It comes from three places:

- **Idle workers.** Beam-4 and greedy-wait keep everything local, which leaves nodes 1 and 2 idle for 25–37 s while node 0, which holds half the data, works through its queue.
- **Moving the wrong data.** HEFT and stealing balance load by moving whole 16 MiB source reads (72 of them, 1.13 GiB, for HEFT on the competing graph). The optimal fluid plan for the competing graph moves none of them; it sends the 1 MiB intermediate outputs to the idle nodes instead.
- **Per-dispatch overhead.** Each dispatch costs 6–22 ms, which adds up to 6–21 s on the critical worker. This overhead rises with the number of bytes a policy moves (Spearman ρ = 0.93 and 0.95, 30 runs each) and only weakly with planning time (ρ = 0.20 and 0.37).

The algorithm families that address these causes are old, and their planning cost is milliseconds: fluid or min-cost-flow placement computed once and then followed by a cheap dispatcher (Quincy, Firmament, Corral, and Bertsimas-style fluid rounding), and locality dispatch that weighs one node's backlog against the others' (delay scheduling with a finite wait; JSQ-MaxWeight). The rollout and receding-horizon literature does guarantee that lookahead is no worse than its base heuristic, but only under the forecast model. That model underestimates the measured per-task write time by about 10 ms (12–14 ms measured against 0.3–2.2 ms modelled) and leaves out dispatch overhead entirely. The guarantee therefore does not carry over to wall time here.

My ranked recommendation:

1. An LP-guided plan with output placement, executed by a cheap dispatcher.
2. A drain-time-aware locality rule, as the cheap online control that the LP plan has to beat.
3. Pipelined dispatch, which hides per-task overhead. It is a runtime change and needs Aakash's approval first.

## 1. Locality against waiting: when waiting pays

Delay scheduling [1] is the canonical locality-versus-waiting policy, and the lab's greedy-wait is essentially an instance of it. When the job that should run next cannot launch a local task, it waits for a bounded number of scheduling opportunities (the skip count D) while other jobs take the slot. The paper's analysis shows that non-locality falls exponentially with D. It also shows that the waiting needed for a target locality level is a fraction of the mean task length and shrinks as slots per node increase [1]. The paper names two conditions under which the analysis breaks: long tasks, and *hotspots*, meaning nodes that many jobs want. For these, after D skips a job may launch "arbitrarily many non-local tasks" until it gets a local one again [1].

The argument relies on two things the lab does not have: many concurrent jobs, so that a slot given up for locality is used by someone else, and a wait that is short relative to the task. The lab runs one DAG on three single-slot workers, so a worker that waits simply sits idle. Node 0 is also a structural hotspot: the ingest pattern `(0, 0, 1, 2)[i % 4]` in `experiments/run.py` places half of every dataset on it.

This explains the split seen in the refinement campaign, where waiting gained 2.0% on the independent workload and lost 6.9% on the competing one. On the independent workload, waiting beats plain greedy because greedy moves 214 source tasks (3.3 GiB) and pays about 0.39–0.44 s for each remote 16 MiB read, against 21–23 ms for a local one (per-node p10/p50 measured stage times, `headroom/out-baseline-large400/headroom.json`). Waiting forever is also wrong, though. Beam-4 and greedy-wait move zero bytes there, and workers 1 and 2 then sit idle for a median of 25–35 s each, while worker 0 runs 1,024 tasks (`gaps.txt`). On the competing graph the hotspot is worse and greedy-wait's planning cost is large: 54.6 s of scheduler time in the baseline, because it re-simulates every candidate. The idle gaps on workers 1 and 2 reach 46–55 s.

The queueing literature states the right rule more sharply than delay scheduling does. Wang et al. model map scheduling with a local service rate α and a slower remote rate γ, and their policy combines join-the-shortest-queue routing with MaxWeight service at each server [9]. They prove it throughput-optimal and heavy-traffic optimal: it asymptotically minimizes backlog as load approaches capacity [9]. The decision compares backlogs weighted by local and remote service rates, which costs O(1) per decision. That is a direct replacement for greedy-wait's per-candidate re-simulation.

KMN [10] adds a related idea: give the scheduler choice by launching a few extra upstream tasks so that downstream transfers can avoid congested links. It reports an 81% reduction in average job duration with 5% extra resources. It does not transfer here, because cephdag tasks have no input choice and spare work has nowhere to run.

## 2. Global placement computed cheaply: flow, LP, and fluid relaxations

Quincy cast cluster scheduling as min-cost flow over a graph whose edge costs encode locality, fairness, and starvation. It reported that the global solutions "substantially outperform greedy scheduling approaches" [2]. Firmament made the same formulation fast. It found that the relaxation MCMF algorithm, together with incremental re-optimization, gives sub-second placements even with 12,500 machines and 150,000 live tasks. It also found that terminating MCMF solvers early gives "unacceptably poor and volatile placements" [3].

Corral addresses joint data and compute placement, which is the lab's setting. An offline planner uses predictable job characteristics to choose where each job's data and tasks go and when the job starts. Execution is decoupled from the planner and treats its output only as guidelines. Corral reports 10–33% lower makespan than YARN's capacity scheduler [4]. Graphene builds an offline schedule that places long-running and hard-to-pack tasks first, distills it into a precedence order, and enforces that order online. It reports 25–75% better average job completion time and 50–100% better makespan, depending on the baseline. It also proves that any DAG-oblivious scheduler can be Ω(d) times optimal, where d is the number of resources [5]. Tetris packs multiple resources and reports 30% gains, about 80% of a simple upper bound on the achievable gain [6].

The operations-research version of the same idea is the fluid relaxation. Replace discrete jobs with continuous flow, solve the resulting LP, and then dispatch so the discrete schedule stays close to the fluid one. For high-multiplicity job shops this is asymptotically optimal. The makespan variant cited by Bertsimas, Gamarnik and Sethuraman stays within an additive term, set by the number of job types, stages, and the largest processing time, of the fluid lower bound [18]. The paper is candid about the limit: at low to moderate multiplicity, simple heuristics are comparable or better [18].

The lab's two large workloads are about as high-multiplicity as a DAG can be. One is 2,048 copies of one task. The other is 512 copies of the same six-task motif, built in `experiments/alternatives.py`. That is why I used this family for the headroom bound (section 5), and why the LP solution reads as a scheduling recipe:

- **Independent workload.** Move about 1.34 GiB, roughly 85 node-0 tasks, to nodes 1 and 2. HEFT and stealing move 106 tasks and beam-4 moves none.
- **Competing workload.** Run every source-reading task (a and b, 16 MiB in, 1 MiB out) on the node that stores its shard. Send node 0's downstream c, d, e, f work to nodes 1 and 2 by writing the 1 MiB intermediates there, about 0.48 GiB in total. HEFT does something else: it moves 72 source tasks, which is 1.13 GiB of 16 MiB reads (`remote_mix.txt`).

Solving these LPs takes 4 ms and 27 ms (`time_lp.txt`). That is three orders of magnitude below beam-4's 7.6–14.7 s of planning.

## 3. Ordering transfers instead of placing tasks

Coflow scheduling treats the flows of a communication stage as one unit. Varys orders coflows by Smallest-Effective-Bottleneck-First and reports communication stages finishing up to 3.16× faster on average than with per-flow mechanisms [11]. Aalo needs no prior knowledge of coflow sizes. It uses discretized least-attained-service priority queues and lands within 12% of Varys on single-stage coflows [12]. Sincronia shows that, given the right ordering, any work-conserving per-flow rate allocation that respects the ordering is within 4× of optimal average coflow completion time [13]. On the task-graph side, contention-aware list scheduling places communications onto the links of a topology graph as edges, and Sinnen and Sousa show that classic list scheduling extends to this model with more accurate schedules [14]. (The earlier scheduling review already covers the 2004 contention-aware lineage.)

These methods help when the network is the bottleneck. In the lab it is not. In both fluid LPs built from measured stage times, the binding constraints are the three worker slots. Node 0's storage read sits at 18–35 s, below the bound, and the shared-link and separate-TX/RX variants give identical bounds. Ousterhout et al. reached the same conclusion for Spark: CPU, not I/O, was often the bottleneck, and better network performance could cut job completion time by a median of at most 2% [8].

There is one network effect the forecast model does not capture. The per-task dispatch and completion-notice overhead rises steeply with the bytes a run moves (ρ = 0.93 independent, 0.95 competing, n = 30 each), but only weakly with planning time (ρ = 0.20 and 0.37; `correlate.txt`). A plausible cause, not established, is that coordinator RPCs share the shaped link with bulk transfers. If that is right, Sincronia's practical message applies at small scale: give a small class of traffic, here control RPCs, priority in the transport. That is a lab and runtime fix rather than a scheduling algorithm, and a diagnostic should come first (shortlist item 3).

## 4. Lookahead with a guarantee: rollout and receding horizon

Rollout is the principled form of what beam search attempts. At each step, try each candidate action, complete the rest of the problem with a base heuristic, and take the action whose completion is cheapest. Bertsekas, Tsitsiklis and Wu show that if the base heuristic is *sequentially consistent*, rollout terminates and is no worse than the heuristic [15]. The survey extends this to *sequentially improving* heuristics and reports that rollout typically improves substantially on the base heuristic, "at the expense of a substantial but often tractable (polynomial) increase in computational requirements" [16]. Bertsekas and Castañon extend rollout to stochastic scheduling, with Monte Carlo estimates of the base policy's cost [17].

Three points matter for the lab.

1. **The guarantee holds only under the model.** Measured p50 stage times differ from the calibrated model in ways that change decisions. A 128 KiB local write takes about 12 ms against 0.27 ms modelled; a 1 MiB local write takes 13–15 ms against 2.2 ms. A 16 MiB local read takes about 23 ms against 34 ms modelled on nodes 0 and 1. About 6 ms per task falls outside the read, compute, and write stages, and the 6–22 ms dispatch overhead is not modelled at all. Lookahead that is "no worse than HEFT" under this model can still lose in wall time.
2. **Beam-4 is not a rollout and has no such guarantee.** Its truncated search can prune the base heuristic's own trajectory. In the competing case it settled on a plan that moved 0.078 GiB and left two workers idle for 33.6–36.6 s.
3. **Full rollout is expensive.** Rolling out a full base policy for every candidate at every decision costs about N² simulated dispatches, around 10⁷ for the 3,072-task graph. Truncated rollout, with a cheap terminal cost such as the fluid LP bound, is the standard way to cut this cost, but I found no measurement of it on DAG makespan with communication.

The cheaper "one-step lookahead" heuristics give modest, inconsistent gains. Lookahead-HEFT evaluates the effect of each placement on the task's children. The authors report that it can shorten makespan "in some cases by up to 20% on average", especially when communication is expensive relative to computation, at a running time they describe as not prohibitive [21]. PEFT obtains a lookahead through an optimistic cost table at HEFT's O(v²·p) complexity and reports beating other list schedulers [22]. In the lab, PEFT and CPOP matched HEFT on the independent workload (68.62 and 68.42 s against 68.30 s) and lost on the competing one (80.73 and 80.35 s against 74.80 s). The adversarial PISA study finds instances where CPoP is significantly better than HEFT, even on a dataset where HEFT does slightly better on average, and argues that average-case benchmarks hide when each heuristic fails [23]. A controlled equal-budget study from October 2026 found MCTS 2.88% worse than randomized-restart list scheduling given the same number of evaluated schedules. Where the list-heuristic baseline already met the lower bound, it found "no headroom for either method to exploit" [24]. (That study used a synthetic, unit-speed domain, not a storage system.)

## 5. Robustness, drift and re-planning

The refinement campaign's medians drifted from about 80 s to 110 s, which is larger than most gaps between policies. The established response to forecast error is not to plan harder at every event. It is to plan cheaply and re-plan selectively.

Sakellariou and Zhao re-schedule only when a task starts later than its planned start by more than its slack. They match "reschedule before every task" while re-planning before fewer than 30% of tasks and using at most 43% of the running time [19]. Canon and Jeannot find that many robustness metrics are equivalent, largely because of the central limit theorem. They report that the makespan standard deviation is a sufficient measure in practice and that mean makespan and its standard deviation are fairly highly correlated. A faster robust HEFT variant (Rob-HEFT) gives results similar to its slower Monte Carlo counterpart [20].

For cephdag this suggests the following. Compute the fluid plan from *measured* per-node rates. Execute it with a cheap dispatcher. Re-solve the LP, which takes milliseconds, only when observed node throughput drifts outside a band. The drift itself remains unexplained; memory reclaim or host contention is suspected. A scheduler cannot fix it, but a scheduler that re-plans when it occurs at least does not lock in the wrong placement.

## 6. When to compute at storage: near-data processing

The computational-storage literature converges on one pattern: run data-reducing work at the storage, and let the loaded side push work back when it is busy.

- Summarizer splits work between the SSD's embedded cores and the host dynamically. It reports up to 20% faster than host-only and 6× faster than SSD-only [27].
- λ-IO dispatches each request dynamically to whichever side, host or device, its execution-time model predicts to be faster, and refreshes the model by profiling a sample of requests periodically [30].
- Adaptive pushdown lets the *storage layer* accept a pushdown request or push it back to the compute layer when its own resources are insufficient. It reports up to 1.9× over both no pushdown and eager pushdown on TPC-H [29].
- FlexPushdownDB combines caching and pushdown through "separable operators" [28].
- hybridNDP splits each query between host and device using a cost model and reports up to 4.2× over host-only execution [31].
- Huawei's Taurus treats near-data processing on its page stores as best-effort, so that it does not hurt other tenants [35].
- OffloadFS (2026) offloads I/O-intensive tasks mainly to a disaggregated storage node, with the option of using peer compute nodes [32].

On the lab's competing graph, the fluid optimum follows this pattern exactly. The reducing operators (a, b: 16 MiB to 1 MiB) stay at the storage that holds the shard. Downstream work on the reduced data goes wherever a worker is free. That is a pushdown boundary set by data reduction, not a per-task earliest-finish-time test.

## 7. Learned scheduling: 2025–2026 update

Since the earlier notes, the 2026 learned DAG schedulers I read claim decision cost comparable to heuristics, but none measures makespan with planning time included on a real storage system. Zhou et al. (arXiv, June 2026 revision) report better makespan than strong baselines on TPC-H query DAGs and ML-compiler graphs, "with inference time comparable to classical heuristics and faster than multi-round neural schedulers" [26]. Cheney and Nyirenda (October 2026) compare PPO, A2C and DQN to heuristics on two Slurm traces with paired seeds and non-parametric tests. On the homogeneous trace, no masked DRL treatment significantly beat a masked uniform-random control. On the heterogeneous trace, MaskablePPO matched the heuristics' waiting time within ±10% [25]. I found no 2025–2026 learned scheduler evaluated on joint compute and output placement over a shared storage cluster. Training one for a 3-node lab would cost far more than the LP, which already computes the target placement.

## 8. The case against a fancier scheduler

Evidence on this side is strong.

- **Network optimization rarely pays.** Ousterhout et al. found it improves Spark job completion by a median of at most 2% [8].
- **Fluid plans beat heuristics only at high multiplicity.** At low multiplicity, simple heuristics match or beat them [18]. That favours the LP plan for these synthetic workloads and argues against it for the irregular DROID DAGs, where the October results already showed greedy, CP-SAT, rollout and MCTS within about 1% of each other.
- **Equal-budget comparisons favour simple search.** The equal-budget MCTS study found simple restarts better and no headroom where the heuristic already reaches the bound [24].
- **Dask's queuing experiments point the same way.** Sending each worker more than one task at a time (worker-saturation > 1.0) gave no definitive runtime improvement, only 2–8% where it helped, and up to 30% more memory. The maintainers advise that tasks whose runtime is on the order of network latency plus scheduler overhead should be made bigger [33][34].
- **The lab's own numbers.** Planning time is 13% of beam-4's wall time on the independent workload and 19% on the competing one, and 50–56% for greedy-wait. The best policy loses to the bound mainly through idle workers and per-task overhead, not through a lack of lookahead.

If Demo 3 has to show the research question answered, the honest answer from this evidence is: lookahead over the current forecast model does not pay for its planning cost here. A one-shot global plan computed from measured rates might, and it costs milliseconds.

## Shortlist (ranked)

All planning-cost figures are expected values, not measurements of a policy, because none of these policies exists yet. Writing them is Aakash's to decide.

| # | Algorithm | Mechanism on our workloads | Expected planning cost | Main risk | Smallest discriminating experiment |
|---|---|---|---|---|---|
| 1 | **Fluid-LP-guided placement with output placement** (Quincy/Firmament-style global placement [2][3]; Corral's plan-then-guide [4]; fluid rounding [18]) | Solve the class-level LP once from measured per-node stage rates. Dispatch greedily, ready tasks first, to keep each (class, origin) share on track. On the competing graph: run all a/b tasks at their shard's node, write node-0 intermediates to nodes 1 and 2, and run c–f there. Targets HEFT's 1.13 GiB of moved source reads and beam's 33.6–36.6 s of idle workers. On the independent workload: move about 85 node-0 tasks, not 106 and not 0. | LP: 4 ms and 27 ms (`time_lp.txt`). Dispatch: O(classes) per event, so total should sit below HEFT's 1.7–3.1 s. | The fluid plan ignores integrality, the tail, and dependency timing. Node 0 could run out of a/b work while c–f wait on remote writes. Measured rates drift (80→110 s). Mitigation: re-solve on drift (§5). | Competing 8 GiB at 400 Mbit, 5 paired repeats against HEFT and beam-4. Predictions: zero moved a/b tasks, about 0.5 GiB crossing as intermediates, idle time on workers 1 and 2 under 5 s, planning under 1 s, wall below 74.8 s. A miss on idle time or moved source reads falsifies the mechanism even if wall time improves by chance. |
| 2 | **Drain-time-aware locality dispatch** (delay scheduling with a finite, backlog-based wait [1]; JSQ-MaxWeight-style weighted backlogs [9]) | Each worker runs local work first. An idle worker takes a remote node-0 task only when node 0's remaining local backlog × its measured per-task time exceeds the measured remote task time, about 0.4–0.45 s. This keeps beam's zero-waste locality but uses the idle 25 s on workers 1 and 2 late in the run. It is the cheap control that #1 has to beat. | O(1) per decision; below stealing's 3.9 s and far below greedy-wait's 33–55 s. | It is myopic about DAG structure: on the competing graph it would still move 16 MiB source reads unless it also places outputs. Rate estimates drift. | Independent 32 GiB, 5 paired repeats against beam-4 and stealing. Predictions: about 85 moved tasks (80–90), worker 1 and 2 idle under 5 s, wall between the about 45 s target and beam's 58.5 s. |
| 3 | **Pipelined dispatch / per-worker queue depth 2** (late binding [7] and Dask worker-saturation [33][34]) | Hides the 6–22 ms dispatch-and-notice round trip per task, which is 6.2 s on beam-4's critical worker (independent) and 15.7 s on HEFT's (competing). Orthogonal to #1 and #2. | No added planning. | Committing tasks early hurts balance; Sparrow's late binding exists to avoid exactly that [7]. Dask measured only 2–8% gains [33]. It changes the runtime, not just the policy, so it needs his approval. | First a diagnostic, with no policy change: from existing traces, compare per-task overhead in low-traffic and high-traffic runs (already ρ ≈ 0.93–0.95), then probe RPC latency on an idle against a saturated shaped link. Only if control traffic is the cause, try HEFT at depth 2 on the independent workload against depth 1. |

**Not recommended now: HEFT-rollout or truncated rollout.** It has the only formal no-worse-than-base guarantee [15][16], but only under a model that misprices writes by about 10 ms per task and omits dispatch overhead. Its planning cost would be far above beam's. Revisit only if #1 leaves a gap and the forecast model is first recalibrated to the measured stage times.

## Headroom estimate

**Method.** `headroom/headroom.py` builds a fluid LP over task classes:

- one class for the independent workload;
- the six motif classes a–f, by shard origin and parity, for the competing workload.

Each class's tasks may be split fractionally across workers, and every output may be stored on any node. Variables per consumer edge tie each read to the store actually chosen, so a single remote write that serves two co-located consumers is charged only once. The LP minimizes T subject to these constraints:

- each worker's slot time (read + compute + write + unstaged time, run sequentially as in the real single-slot worker) is at most T;
- each node's storage read and write byte load, divided by the calibrated rate, is at most T;
- each node's TX and RX load is at most T, either separately or as a shared link.

Three sets of stage times are used: the calibrated model in `live-results/models.json` (calibration 8b52c1236b614354a97c49337f56ba82), and the per-node 10th and 50th percentile stage times measured across the 30 baseline runs of each workload. Any real schedule is at least T under the same rates, because it is integral and contended and it also pays dispatch and planning. The bound excludes the measured 6–22 ms per-task dispatch overhead and all planning time.

| Case (baseline 2026-11-03, 400 Mbit) | Model LB | Measured-p10 LB | Measured-p50 LB | All-local corner, p50 | Best measured (median of 3) | Best ÷ p50 LB |
|---|---|---|---|---|---|---|
| Independent, 32 GiB, 2,048 tasks | 35.3 s | 37.5 s | 40.9 s (moves 1.34 GiB) | 44.6 s | beam-4 58.54 s (planning 7.61 s) | 1.43 |
| Competing, 8 GiB, 3,072 tasks | 28.8 s | 36.2 s | 40.9 s (moves 0.48 GiB) | 55.2 s | HEFT 74.80 s (planning 3.10 s) | 1.83 |

Adding beam-4's measured minimum dispatch overhead (about 6.1–6.5 ms per task spread over three workers, +4.2 s and +6.6 s) gives practical targets of about 45 s and 47.5 s. Those are 23% and 37% below the best measured policies. They are targets, not guarantees: the fluid plan ignores the tail and integrality.

**Where the best policies lose.** The figures are per-worker medians from `gaps.txt`; head + busy + overhead + gaps + tail add up to wall time.

- **Beam-4, independent.** Worker 0 is busy for 49.24 s, with 6.23 s of dispatch overhead and 2.98 s of gaps. Workers 1 and 2 have gaps of 25.6 s and 25.8 s, and no bytes move.
- **HEFT, competing.** Worker 0 is busy for 55.57 s, with 15.66 s of overhead and 2.92 s of gaps. 1.13 GiB of 16 MiB source reads move.
- **Beam-4, competing.** Workers 1 and 2 have gaps of 36.6 s and 33.6 s, and planning takes 14.69 s.

Under the measured rates, balancing is worth about 3.7 s on the independent workload (the 44.6 s all-local corner against the 40.9 s LB) and about 14 s on the competing one (55.2 s against 40.9 s). That is why the competing graph is the better test of shortlist item 1.

**Caveats.**

- The p10 and p50 stage times come from the same runs the policies produced, so they include the policies' own contention. A policy that moves less data might see faster stages.
- Stage medians are pooled across policies per node.
- I analyzed the baseline campaign only. The refinement v2 runs, which carry the 80→110 s drift, are not in these numbers.
- The bound assumes the forecast model's sequential-stage task structure.

**Reproduce.** All scripts are in `/home/aakash/projects/comp-storage/literature/headroom/`, and all results are in `out-baseline-large400/` (`headroom.txt`, `headroom.json`, `runs.csv`, `gaps.csv`, `gaps.txt`, `remote_mix.txt`, `correlate.txt`, `time_lp.txt`).

```
.venv/bin/python -I headroom.py ~/.hermes/experiments/computational-storage-baseline-20261103/live-results/alternatives/large-400 out-baseline-large400
.venv/bin/python -I gaps.py <same dir> out-baseline-large400/gaps.csv
.venv/bin/python -I remote_mix.py <same dir>
.venv/bin/python -I correlate.py out-baseline-large400
.venv/bin/python -I time_lp.py ~/.hermes/experiments/computational-storage-baseline-20261103/live-results/models.json
```

The medians the scripts report match the baseline report: beam-4 58.54 s, HEFT 68.30 s and 74.80 s, greedy-wait 66.03 s and 96.86 s.

## Coverage

**Searched** with the web search tool on 2026-11-12, by name and venue:

- delay scheduling (EuroSys'10), Quincy (SOSP'09), Firmament (OSDI'16), Corral (SIGCOMM'15), Graphene (OSDI'16), Tetris (SIGCOMM'14), Sparrow (SOSP'13), Ousterhout (NSDI'15), KMN (OSDI'14), MapReduce locality heavy-traffic optimality;
- Varys, Aalo, Sincronia, one-port and contention-aware list scheduling (Sinnen and Sousa);
- Bertsekas rollout (1997, 1999, survey), receding-horizon and MPC DAG scheduling, Sakellariou and Zhao rescheduling, Canon and Jeannot robustness, stochastic and robust HEFT, Bertsimas fluid relaxations;
- lookahead HEFT, PEFT, IPEFT, dynamic critical path, SAGA and PISA;
- Summarizer, FlexPushdownDB, λ-IO, near-data offload under load from 2024–2026;
- learned DAG scheduling and MCTS from 2025–2026;
- Dask worker-saturation.

**Read in full or substantially** (full-text PDF downloaded, then targeted sections read): delay scheduling, Quincy, Firmament, Corral, Graphene, Tetris, Sparrow, Wang et al. INFOCOM'13, Varys, Aalo, the three Bertsekas rollout papers, PISA, the October 2026 equal-budget MCTS study, the October 2026 DRL statistical evaluation, Zhou et al. 2026, Summarizer, FlexPushdownDB, Adaptive pushdown, λ-IO, hybridNDP.

**Abstract, search-result content, or a few sections only:** Ousterhout NSDI'15 (abstract page), KMN, Sincronia, Sinnen and Sousa, Sakellariou and Zhao (the publisher page with body text), Canon and Jeannot (abstract and conclusion), lookahead-HEFT (abstract and conclusion), PEFT (abstract), Bertsimas et al. (abstract and introduction), Taurus NDP, OffloadFS, MoSAIC (arXiv 2610.15205, an LLM-guided heuristic for HLS-scale DAGs; not cited because its domain is far from ours), Hopper (speculation; not relevant here).

**Not covered:**

- IPEFT and dynamic-critical-path variants: no source opened, so no claim is made.
- The SAGA benchmark's numerical results.
- Formal MPC and receding-horizon DAG papers: the search turned up nothing I could verify as directly relevant.
- Robust HEFT variants beyond Canon and Jeannot.
- 2024–2026 computational-storage scheduling work beyond the papers listed above.
- Analysis of the refinement v2 run data.

The prior reviews already cover WOW, Skyhook, OASIS, Moirai, the hotspot-aware NSDI'25 work, TaskVine, Diffuse, Decima, SPADE, and the one-shot DAG RL work; I did not revisit them.

**Not verifiable:**

- The Manchester-hosted PDFs of lookahead-HEFT and Sakellariou and Zhao timed out, so those claims come from the DOI and publisher pages and search-result text of the papers.
- USENIX PDFs for Firmament, Graphene and NSDI'15 returned 404 to the fetcher; the Firmament and Graphene texts come from the authors' own copies.
- The Bertsimas-Sethuraman makespan bound is cited as stated in the OR 2003 paper, not from the original 2002 paper.

## Sources

[1] https://people.eecs.berkeley.edu/~matei/papers/2010/eurosys_delay_scheduling.pdf — Zaharia et al., Delay Scheduling: A Simple Technique for Achieving Locality and Fairness in Cluster Scheduling, EuroSys 2010
[2] https://www.sigops.org/s/conferences/sosp/2009/papers/isard-sosp09.pdf — Isard et al., Quincy: Fair Scheduling for Distributed Computing Clusters, SOSP 2009
[3] https://people.csail.mit.edu/malte/pub/papers/2016-osdi-firmament.pdf — Gog et al., Firmament: Fast, Centralized Cluster Scheduling at Scale, OSDI 2016
[4] https://www.microsoft.com/en-us/research/wp-content/uploads/2016/12/corral_sigcomm15_final.pdf — Jalaparti et al., Network-Aware Scheduling for Data-Parallel Jobs: Plan When You Can (Corral), SIGCOMM 2015
[5] https://pages.cs.wisc.edu/~rgrandl/papers/graphene.pdf — Grandl et al., Graphene: Packing and Dependency-Aware Scheduling for Data-Parallel Clusters, OSDI 2016
[6] https://tetris.cs.wisc.edu/files/tetris_sigcomm14.pdf — Grandl et al., Multi-Resource Packing for Cluster Schedulers (Tetris), SIGCOMM 2014
[7] https://www.sigops.org/s/conferences/sosp/2013/papers/p69-ousterhout.pdf — Ousterhout et al., Sparrow: Distributed, Low Latency Scheduling, SOSP 2013
[8] https://www.usenix.org/conference/nsdi15/technical-sessions/presentation/ousterhout — Ousterhout et al., Making Sense of Performance in Data Analytics Frameworks, NSDI 2015 (abstract page)
[9] https://www.cs.cmu.edu/~weinaw/pdf/MapReduceinfocom13.pdf — Wang et al., Map Task Scheduling in MapReduce with Data Locality: Throughput and Heavy-Traffic Optimality, INFOCOM 2013
[10] https://www.usenix.org/conference/osdi14/technical-sessions/presentation/venkataraman — Venkataraman et al., The Power of Choice in Data-Aware Cluster Scheduling (KMN), OSDI 2014
[11] https://www.mosharaf.com/wp-content/uploads/varys-sigcomm14.pdf — Chowdhury, Zhong, Stoica, Efficient Coflow Scheduling with Varys, SIGCOMM 2014
[12] https://www.mosharaf.com/wp-content/uploads/aalo-sigcomm15.pdf — Chowdhury, Stoica, Efficient Coflow Scheduling Without Prior Knowledge (Aalo), SIGCOMM 2015
[13] https://dl.acm.org/doi/10.1145/3230543.3230569 — Agarwal et al., Sincronia: Near-Optimal Network Design for Coflows, SIGCOMM 2018 (abstract page)
[14] https://researchspace.auckland.ac.nz/items/f944320a-dce8-483b-a672-1980087d1796 — Sinnen, Sousa, Communication Contention in Task Scheduling, IEEE TPDS 16(6) 2005 (abstract page)
[15] https://web.mit.edu/dimitrib/www/rollout.pdf — Bertsekas, Tsitsiklis, Wu, Rollout Algorithms for Combinatorial Optimization, Journal of Heuristics 3, 1997
[16] https://web.mit.edu/dimitrib/www/Rollouts_Survey.pdf — Bertsekas, Rollout Algorithms for Discrete Optimization: A Survey, Handbook of Combinatorial Optimization 2013
[17] https://web.mit.edu/dimitrib/www/quiz.pdf — Bertsekas, Castanon, Rollout Algorithms for Stochastic Scheduling Problems, Journal of Heuristics 5, 1999
[18] http://www.columbia.edu/~js1353/pubs/bgs-or03.pdf — Bertsimas, Gamarnik, Sethuraman, From Fluid Relaxations to Practical Algorithms for High-Multiplicity Job-Shop Scheduling: The Holding Cost Objective, Operations Research 2003
[19] https://doi.org/10.1155/2004/930924 — Sakellariou, Zhao, A Low-Cost Rescheduling Policy for Efficient Mapping of Workflows on Grid Systems, Scientific Programming 12(4) 2004
[20] https://doi.org/10.1109/tpds.2009.84 — Canon, Jeannot, Evaluation and Optimization of the Robustness of DAG Schedules in Heterogeneous Environments, IEEE TPDS 21(4) 2010
[21] https://doi.org/10.1109/pdp.2010.56 — Bittencourt, Sakellariou, Madeira, DAG Scheduling Using a Lookahead Variant of the HEFT Algorithm, PDP 2010
[22] https://doi.org/10.1109/tpds.2013.57 — Arabnejad, Barbosa, List Scheduling Algorithm for Heterogeneous Systems by an Optimistic Cost Table (PEFT), IEEE TPDS 25(3) 2014
[23] https://arxiv.org/abs/2403.07120 — Coleman, Krishnamachari, PISA: An Adversarial Approach to Comparing Task Graph Scheduling Algorithms, arXiv 2403.07120
[24] https://arxiv.org/abs/2610.12542 — Zhang, Equal-Budget Monte Carlo Tree Search versus Randomized-Restart List Scheduling for DAG Workflows, arXiv 2610.12542 (Oct 2026)
[25] https://arxiv.org/abs/2610.21008 — Cheney, Nyirenda, Deep Reinforcement Learning for HPC Job Scheduling: A Statistical Evaluation, arXiv 2610.21008 (Oct 2026)
[26] https://arxiv.org/abs/2603.23249 — Zhou et al., A Learning Method with Gap-Aware Generation for Heterogeneous DAG Scheduling, arXiv 2603.23249 (v2 Jun 2026)
[27] https://intra.engr.ucr.edu/~htseng/files/MICRO2017_Summarizer.pdf — Koo et al., Summarizer: Trading Communication with Computing Near Storage, MICRO 2017
[28] http://vldb.org/pvldb/vol14/p2101-yang.pdf — Yang et al., FlexPushdownDB: Hybrid Pushdown and Caching in a Cloud DBMS, PVLDB 14 2021
[29] https://arxiv.org/abs/2312.15405 — Yang et al., Enhancing Computation Pushdown for Cloud OLAP Databases (Adaptive pushdown), arXiv 2312.15405
[30] https://www.usenix.org/system/files/fast23-yang-zhe.pdf — Yang et al., lambda-IO: A Unified IO Stack for Computational Storage, FAST 2023
[31] https://openproceedings.org/2025/conf/edbt/paper-228.pdf — Knoedler, Ramzan, Petrov, hybridNDP: Dynamic Operation Offloading and Cooperative Query Execution in Smart Storage Settings, EDBT 2025
[32] https://arxiv.org/abs/2604.13743 — Moon et al., OffloadFS: Leveraging Disaggregated Storage for Computation Offloading, arXiv 2604.13743 (Apr 2026)
[33] https://github.com/dask/distributed/issues/7213 — dask/distributed issue 7213, Turn on queuing by default (worker-saturation benchmarks), 2022
[34] https://github.com/dask/distributed/pull/6614 — dask/distributed PR 6614, Withhold root tasks (worker-saturation), 2022
[35] https://arxiv.org/abs/2506.20010 — Lin et al., Near Data Processing in Taurus Database, arXiv 2506.20010 (2025)
