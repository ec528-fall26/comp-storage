# Demo 3 pilots, cephdag8 on aakashpc (night of 2026-11-12 to 13)

Three pilot campaigns, one repeat each, run with `experiments/campaign.py` through runjob
`cephdag8-pilots` (00:26 to 01:54 EST, exit 0). The lab ran cephdag demo3 `e81d66c` in every
container, with the v3 calibration, at tbf 400 Mbit. Every run passed verification: the
sampled oracle, a checksummed read of every final output, and output checksums that agree
across policies and both modes. Full tables, with tasks per worker and step durations, are in
`pilots/pilot-{sweep,droid,skew}.md`. Raw results are in
`aakashpc:/mnt/mass/cephdag/campaigns/pilot-*/`.

"Payload GB" counts the bytes of task inputs and outputs that crossed between nodes, taken
from their stored sizes. Times are in seconds. Speedup means disaggregated best divided by
computational-storage best.

## Before the pilots

Four fixes went into demo3 (commits `5dcfac8`..`e81d66c`, plus `d2ae289`, which changes a
test only). All 201 tests pass, including the unchanged equivalence and wire tests.

1. **Calibration.** It now samples 32 and 64 MiB tasks, in rounds that put one sample on
   every worker at once.
2. **Output placement on role-split clusters.** Outputs go wherever the fifo-hash stable
   hash puts them, and policies choose only the task and the worker.
3. **Mode switches.**
   - Each repeat runs all of its cases in one mode, then all in the other.
   - A full verify runs at session start and every 6 h. Every other switch gets a light
     `lab.py check`.
4. **Deployment.** Code was copied into `/app` without recreating any container.
   `/mnt/mass/cephdag/deploy/deployed-commit.json` records the commit and the tree hashes,
   and all 17 containers match.

The ~2 GiB of police-attempt calibration objects were removed.

Calibration on the 2 GiB, 64 MiB synthetic smoke workload: the median per run of actual
compute-stage seconds divided by model seconds (`demo3/compute_ratio.py`, runs in
`smoke/ratio-v3`).

| mode, iterations | v1 model (4/16 MiB, one at a time) | v3 model (32/64 MiB, rounds) |
|---|---|---|
| cs, i1 | 1.94–3.53 | 0.78–1.43 |
| cs, i128 | 5.98–7.46 | 0.68–0.85 |
| disagg, i1 | 1.46–1.59 | 0.24–0.26 |
| disagg, i128 | 5.62–6.24 | 0.78–0.87 |

The v3 compute fit, in seconds per byte:

- cs: iteration 4.86e-10, output 5.19e-10, input 0, fixed 0; RMSE 0.36 s.
- disagg: input 6.84e-10, iteration 4.03e-10, output 6.75e-10; RMSE 0.33 s.

v1 had iteration 5.36e-11, about 9 times lower. DROID costs 50.5 CPU-s per source GB in cs
and 50.7 in disagg. The target of about 1.5x is met everywhere except disagg at i1, where the
model over-predicts 4x on tasks that take about 0.1 s.

## P1: intensity sweep (4 GiB, 64 MiB shards, round-robin, ratio 0.25, 29.6 min)

| iterations | cs heft | cs data-local | cs beam-4 | disagg fifo-hash | disagg heft | speedup (best/best) |
|---|---|---|---|---|---|---|
| 1 | **2.80** | 4.02 | 2.96 | 19.08 | **18.09** | 6.46 |
| 2 | **2.92** | 4.34 | 2.99 | 18.97 | **18.17** | 6.22 |
| 4 | 3.61 | 4.84 | **3.56** | 18.95 | **18.78** | 5.28 |
| 8 | **4.76** | 6.90 | 5.19 | 19.55 | **18.40** | 3.87 |
| 16 | **7.11** | 8.43 | 8.22 | **20.82** | 21.18 | 2.93 |
| 32 | **11.94** | 12.77 | 12.66 | 21.85 | **21.29** | 1.78 |
| 64 | **18.55** | 22.45 | 22.47 | **28.08** | 30.21 | 1.51 |
| 128 | **35.83** | 40.58 | 40.68 | **38.24** | 43.56 | 1.07 |

- **Payload.** cs heft moved 0 GB, beam-4 0–0.27 GB and data-local 0.13–0.40 GB. Disagg
  moved 5.37 GB in every run.
- **Planning cost.** heft took ≤ 0.01 s and beam-4 0.16–0.37 s.
- **Where the curve crosses 1x.** The speedup falls from 6.5x at i1 to 1.5x at i64 and 1.07x
  at i128. Interpolating log-log between i64 and i128 puts 1x at about i150, which is inside
  the noise of one repeat at i128. The first-principles estimate in PLAN.md was about 10x
  while I/O-bound and about 1x above i380. The measured speedup is lower at both ends; the
  first surprise below explains most of it.

## P2: DROID (shards 00002–00005, 3.61 GB source, 128 px, 472 tasks, 17.4 min)

| mode | policy | wall s | policy s | payload GB | verified |
|---|---|---|---|---|---|
| cs | **data-local** | **39.06** | 0.069 | 0.556 | yes |
| cs | beam-4 | 44.79 | 3.185 | 0.524 | yes |
| cs | heft | 54.79 | 0.100 | 4.961 | yes |
| disagg | **heft** | **158.85** | 0.062 | 30.321 | yes |
| disagg | fifo-hash | 179.54 | 0.017 | 30.321 | yes |

- **cs against disagg.** The cs best is 4.07x faster than the disagg best (4.60x against
  fifo-hash) and moves 54x fewer bytes.
- **Within cs.** HEFT moved 9x more data than data-local and was 40% slower. Beam-4 kept the
  data local but spent 3.2 s planning and finished 15% behind data-local.
- **Within disagg.** HEFT beat fifo-hash by 12%.
- **Every output matched:** the output digest was `dae96e245a5f` for all 5 runs.

## P3: skew (8 GiB, 128 × 64 MiB, 65 shards on n0 and 9 on each of n1..n7, ratio 0.25, 20.7 min)

| iterations | policy | wall s | policy s | payload GB | tasks on n0 / others |
|---|---|---|---|---|---|
| 1 | greedy-wait | **17.80** | 13.02 | 0.000 | 65 / 9 each |
| 1 | stealing | 18.50 | 0.09 | 0.738 | 54 / 10–11 |
| 1 | data-local | 18.85 | 0.01 | 1.141 | 53 / 9–12 |
| 1 | greedy | 23.13 | 5.50 | 2.617 | 51 / 9–12 |
| 1 | beam-4 | 29.22 | 0.43 | 2.215 | 53 / 9–15 |
| 1 | heft | 44.68 | 0.01 | 2.684 | 34 / 12–14 |
| 1 | disagg fifo-hash | 93.52 | 0.00 | 10.737 | n/a |
| 16 | greedy-wait | **24.22** | 16.18 | 0.000 | 65 / 9 each |
| 16 | data-local | 25.86 | 0.02 | 1.409 | 49 / 9–13 |
| 16 | greedy | 27.50 | 4.17 | 3.355 | 47 / 9–15 |
| 16 | stealing | 28.31 | 0.09 | 1.141 | 48 / 11–13 |
| 16 | beam-4 | 35.63 | 0.47 | 2.215 | 53 / 8–15 |
| 16 | heft | 60.84 | 0.01 | 3.423 | 24 / 13–16 |
| 16 | disagg fifo-hash | 95.34 | 0.01 | 10.737 | n/a |

The speedup, cs best against disagg, was 5.25x at i1 and 3.94x at i16.

The answer to "does the scheduler matter once compute sits at the storage?" is yes: the spread
is 2.5x, from 17.8 to 44.7 s at i1 and from 24.2 to 60.8 s at i16. But it goes the opposite
way from PLAN.md's hypothesis.

- **Keeping the work on n0 wins.** At these intensities, moving work off n0 does not pay.
  greedy-wait kept every task on its data's node and moved nothing.
- **The policies that move the most data lose.** HEFT moved 2.1–2.8 GB out of n0, and that
  traffic all leaves through n0's one 47 MB/s link: about 45–60 s, which is HEFT's wall
  time. The list model in `cephdag/policies/list.py` (`read_and_compute`, `add_writes`)
  charges each transfer `min(read, tx[source], rx[worker])` and keeps calendars only for
  workers. Several transfers out of n0 are therefore costed as if each had the full link,
  even though they share it.
- **data-local is not strictly local.** It is work-conserving: when n0's worker is busy, it
  hands n0's tasks to idle workers, and so moved 1.1–1.4 GB.

## Surprises

1. **The host's memory bus caps compute.**
   - At 64 MiB, 8 workers computing at once take 0.90 ms per MiB-iteration against 0.11 ms
     for one worker alone (`pilots/cache-probe.jsonl`). So the 8-node cluster computes at
     about the aggregate speed of a single worker. All 16 emulated nodes share one DRAM bus,
     and a 64 MiB working set fits the 9950X3D's L3 only when one worker runs.
   - A cache-blocked loop removes this. Iterating each 1 MiB chunk 32 times, instead of the
     whole array, takes 0.127 s per worker with all 8 at once, against 1.64 s now, and gives
     identical bytes (`pilots/chunk-probe.jsonl`, same sha256).
   - This is why the speedup reaches 1x near i150 rather than i380. On real separate nodes
     the compute-bound end would be about 8x faster, and the crossover would sit much
     higher.
   - **Decision for Aakash or the main assistant:** keep the current kernel and state this
     in the demo, or switch to the chunked kernel. It is ready on the cephdag branch
     `demo3-chunked-numeric` (`9ca8ce7`), with a test that its bytes equal the whole-array
     loop; all 202 tests pass. It is not merged or deployed. Adopting it means merge,
     `deploy_code.py`, copy `models.json` and `calibration.json` to `-v3` names in both calibration
     directories (the script's keep step saves only v2), recalibrate with
     `recalibrate-v3.sh` (about 20 min) and rerun `pilot-sweep` (30 min or less), about 1 h in all.
   - **Decided 2026-11-13 by the main assistant: switch.** See 'Re-pilots with the chunked
     kernel' below. The old-kernel pilots above stay as evidence of the emulation artefact.
2. **The final-output verification read dominates per-run overhead and grows with output
   size.**
   - `coordinator.py` reads every final output from n0 after the timer stops, at about
     50 MB/s (n0's shaped link). Measured: 1.07 GB took 21 s, 2.15 GB took 25–41 s and
     2.57 GB (DROID) took 50 s.
   - Projected: about 17 min per run on a 71.5 GB DROID wave and about 23 min per run at
     256 GiB synthetic.
   - Reading each output on a worker in parallel, still checksummed, would cut this about
     8x in disagg and more in cs. The plan below shows both cases. This is not
     implemented.
3. **Disagg runs well above its per-link bound.** DROID took 159 s against 60 s (the
   busiest node's 2.83 GB at 47 MB/s). The synthetic i1 run took 18.1 s against 11.4 s. The
   router passed 3 Gbit for 8 one-way flows during validation, so the router is not the
   suspect. Candidates are many-to-many collisions on storage-node links under the hash
   placement, and DROID's dependency chain. The cause is unexplained.
4. **Disagg HEFT now beats fifo-hash on DROID and at low intensity, but loses at i64 and
   i128.** At i64 it took 30.2 s against 28.1 s, and at i128 43.6 s against 38.2 s. With hash
   placement it is no longer stuck on node 0.
5. **greedy-wait plans slowly:** 13–16 s on just 128 tasks, overlapped with execution. That
   is unsafe at 4,096 tasks (256 GiB) without a budget.
6. **campaign.py over-estimates by 1.2–2x.** It planned 0.99, 0.35 and 0.44 h for the three
   pilots, against 0.49, 0.29 and 0.35 h actual.

## Measured costs for planning

| item | measured |
|---|---|
| full verify at session start | 185–205 s |
| light switch (mode, shape, check) | about 7 s (4 + 1 + 2) |
| final-output verification read | about 50 MB/s of final outputs per run |
| other per-run overhead | about 2 s synthetic; about 17 s DROID (sampled partition checks) |
| create synthetic dataset | 0.33–0.42 GB/s |
| DROID ingest from the HDD | 132 MB/s |
| DROID wall per source GB | cs data-local 10.8, beam-4 12.4, heft 15.2; disagg heft 44.0, fifo-hash 49.7 |

## What the pilots imply for the full runs

- **Sweep.**
  - Drop i512 and i2048. They lie past the crossover, where both modes are compute-bound,
    and at 32 GiB they would cost about 17 h.
  - Use i1, 4, 16, 32, 64, 128 and 256.
  - In cs, keep heft (best or tied at every intensity) and data-local (the locality
    baseline). Drop beam-4: under even placement it ties heft or loses by up to 21%, with no
    new information.
  - In disagg, keep fifo-hash and heft.
  - Repeats matter more than size here; i128 is within single-repeat noise of 1x.
- **DROID.**
  - Keep cs data-local (best) and disagg heft (the stronger baseline, which makes the
    speedup claim conservative).
  - Beam-4 is optional (+3.7 h at 500 GB). It speaks to the lookahead question, but it was
    15% slower than data-local.
  - Drop cs heft, which moves 9x the data and is the slowest cs policy. Drop disagg
    fifo-hash: 13% slower than heft, and 6.9 h at 500 GB.
  - Each wave is a paired cs/disagg comparison on fresh data, so 7 waves give 7 pairs
    without repeats.
- **Skew.**
  - i1 and i16 only show that locality wins. Add i128, where per-task compute should
    finally outweigh the 1.4 s it takes to move a shard off n0; the full run needs that
    point to test whether moving work ever pays.
  - Drop greedy, which is dominated by greedy-wait and plans for 4–5 s. Keep data-local,
    greedy-wait, stealing, heft and beam-4, with fifo-hash in disagg.
- **Scale.** At 256 GiB the verification read (about 23 min per run) and the disagg walls
  (about 20 min) dominate. One repeat with few policies is affordable; the competing
  workload is not.
- **Terabyte scale is not reachable in this window.** One 500 GB DROID pass with two
  policies takes about 13 h, and only 501.5 GB of DROID has been downloaded.

## Recommended full-run plan (about 30 h of lab time from Friday about 06:30)

Superseded by the chunked-kernel re-pilots and the specs launched below.

The estimates scale the pilot walls linearly with size, add the measured verification read
(50 MB/s of outputs), the switches and one full verify per 6 h. They are not yet confirmed at
full size: check each campaign's first steps against them.

| order | campaign | settings | est. h (now) | est. h with parallel verification |
|---|---|---|---|---|
| 1 | sweep (story 1) | own 16 GiB dataset, 64 MiB shards, round-robin; i 1, 4, 16, 32, 64, 128, 256; cs heft and data-local; disagg fifo-hash and heft; 3 repeats; delete at end | 4.6 | 2.9 |
| 2 | DROID (story 2) | shards 0–549 at 128 px in 7 waves of 79 (about 500 GB); cs data-local; disagg heft; 1 pass (7 paired waves) | 12.9 | 9.4 |
| 2b | optional: add cs beam-4 to DROID | lookahead on the real pipeline | +3.7 | +2.0 |
| 3 | skew (story 3) | 8 GiB, half on n0; i 1, 16, 128; cs data-local, greedy-wait, stealing, heft, beam-4; disagg fifo-hash; 3 repeats | about 2.0 (the i128 walls are not measured, ±50%) | about 1.7 |
| 4 | scale (story 4) | 256 GiB independent, i1 and i32; cs heft and data-local; disagg fifo-hash; 1 repeat | 3.9 | 1.9 |
| | **total** | without 2b / with 2b | **23.4 / 27.1** | **15.9 / 17.9** |

That leaves about 3–7 h of buffer for reruns and drift.

- **Order and disk space.** DROID waves need the Ceph space, so the 256 GiB synthetic
  dataset should be created only in step 4: about 11 min at 0.4 GB/s. Do not keep it from
  the sweep.
- **Before step 1, decide on surprise 1.** Changing the kernel changes the sweep's
  high-intensity half and the location of the crossover, but not DROID.
- **The specs now match this table** (cephdag demo3 `7d49822`, deployed):
  `experiments/campaigns/demo3-{sweep,droid,skew,scale}.json`, with the new `demo3-skew`.
  Optional 2b means adding `beam-4` to the cs policies in `demo3-droid.json`.
- **The estimates differ from campaign.py's.** `campaign.py plan` on aakashpc gives 4.0 h
  for the sweep, 8.6 h for DROID, 1.5 h for skew and 1.8 h for scale. These are below the
  table because the planner does not count the final-output verification read.

## Re-pilots with the chunked kernel (2026-11-13, 01:50–03:14 EST)

**Why the kernel changed** (decided by the main assistant). The numeric kernel iterated
`tanh(x*1.0001+0.0001)` over the whole 64 MiB array once per iteration, so every iteration
streamed 64 MiB through DRAM, and 8 workers sharing aakashpc's one memory bus computed at
about the speed of one (surprise 1). Separate nodes do not share a memory bus, and arithmetic
intensity should mean compute per byte read. The chunked kernel iterates each 1 MiB chunk
`iterations` times; the map is elementwise, so the bytes are identical.

**Code.** `demo3` fast-forwarded to `9ca8ce7` (chunked kernel); 202/202 tests pass on the VM.
`deploy_code.py` put `9ca8ce7` in all 17 containers, then `2b22dd6` (campaign specs only,
`cephdag/` unchanged) before the full runs; `/mnt/mass/cephdag/deploy/deployed-commit.json`
records `2b22dd6` with all 17 trees matching. The ratio smoke reproduced the old kernel's output
digests exactly (`0f318e55a107` at i1, `ac306eca697e` at i128), so the bytes are unchanged on
the lab too.

### Recalibration v4

runjob `cephdag8-recalibrate-v4-20261113-065435` (`demo3/recalibrate-v4.sh`, exit 0, 20 min).
The v3 files are kept as `models-v3.json` and `calibration-v3.json` in both
`/results/calibration/cephdag8-{cs,disagg}/`. Fit in seconds per byte:

| model | fixed s | input | per iteration | output | fit RMSE s |
|---|---|---|---|---|---|
| cs v3 (whole array) | 0 | 0 | 4.86e-10 | 5.19e-10 | 0.36 |
| **cs v4 (chunked)** | 0 | 4.95e-10 | **6.02e-11** | 4.53e-10 | 0.016 |
| disagg v3 | 0 | 6.84e-10 | 4.03e-10 | 6.75e-10 | 0.33 |
| **disagg v4** | 0.0068 | 4.12e-10 | **6.03e-11** | 1.14e-10 | 0.013 |

The per-iteration cost fell 8.1x (cs) and 6.7x (disagg), matching the chunk probe (about 8x).

Median actual/model compute-stage time per run (`compute_ratio.py`, 2 GiB / 64 MiB synthetic,
runs in `/mnt/mass/cephdag/smoke/ratio-v4/`):

| mode, iterations | v3 models | v4 models |
|---|---|---|
| cs, i1 | 1.02–1.09 | 0.94–1.01 |
| cs, i128 | 0.14 | 1.04–1.07 |
| disagg, i1 | 0.24–0.25 | 0.50–0.52 |
| disagg, i128 | 0.16–0.17 | 0.99–1.05 |

Within 1.5x everywhere except disagg i1, where the model over-predicts 2x on tasks of about
0.1 s (it was 4x with v3).

### pilot2-sweep (4 GiB, 64 MiB, round-robin, ratio 0.25, 1 repeat, 25.4 min)

| iterations | cs heft | cs data-local | disagg fifo-hash | disagg heft | speedup (best/best) |
|---|---|---|---|---|---|
| 1 | **2.59** | 4.23 | 18.86 | **17.90** | 6.91 |
| 4 | **2.70** | 4.01 | 19.07 | **18.43** | 6.83 |
| 16 | **3.03** | 4.32 | 19.26 | **18.82** | 6.21 |
| 64 | **4.52** | 5.94 | 21.18 | **20.48** | 4.53 |
| 256 | 10.96 | **10.93** | **27.05** | 27.11 | 2.47 |
| 1024 | 35.69 | **35.58** | **52.11** | 52.71 | 1.46 |
| 2048 | **68.40** | 69.23 | 85.29 | **84.58** | 1.24 |
| 4096 | **134.36** | 134.63 | **150.02** | 150.49 | 1.12 |

- **No crossing.** The speedup falls from 6.9x at i1 to 1.12x at i4096 and never reaches
  1.1x in the piloted range. From i256 up, disagg is slower by a near-constant 15.7–16.5 s:
  the 5.37 GB of input and output that cross the links (14.3 s at 8 × 47 MB/s), because a
  worker does not overlap its transfers with compute. So the speedup tends to
  1 + 16 s / cs wall and approaches 1 without crossing it.
- **Against the old kernel**, cs heft at i1024 (35.7 s) now costs what i128 cost before
  (35.8 s): 8x more compute per second, as on separate machines. The old crossover near i150
  was the memory-bus artefact.
- **Payload.** cs heft moved 0 GB at every intensity, data-local 0–0.20 GB; disagg moved
  5.369 GB in every run.
- **Policies.** In cs, heft is 24–39% faster than data-local up to i64 (data-local is
  work-conserving and moves shards); from i256 they tie. In disagg, heft and fifo-hash are
  within 6% everywhere.
- Every run verified; every case's digest agrees across policies and modes.

### pilot2-skew (8 GiB, 65 shards on n0 and 9 on each of n1..n7, ratio 0.25, 1 repeat, 27.9 min)

| iterations | policy | wall s | policy s | payload GB | tasks on n0 / others |
|---|---|---|---|---|---|
| 128 | beam-4 | **36.32** | 0.46 | 1.812 | 45 / 10–16 |
| 128 | data-local | 37.18 | 0.02 | 1.544 | 42 / 12–13 |
| 128 | stealing | 37.46 | 0.08 | 1.409 | 44 / 11–14 |
| 128 | greedy-wait | 50.77 | 20.72 | 0.000 | 65 / 9 each |
| 128 | heft | 55.68 | 0.01 | 3.892 | 27 / 13–16 |
| 128 | disagg fifo-hash | 95.11 | 0.01 | 10.737 | n/a |
| 1024 | data-local | **98.64** | 0.02 | 2.886 | 22 / 15–16 |
| 1024 | greedy-wait | 103.78 | 23.64 | 4.027 | 23 / 11–18 |
| 1024 | beam-4 | 105.88 | 0.46 | 3.087 | 23 / 14–16 |
| 1024 | stealing | 109.79 | 0.08 | 2.617 | 26 / 14–15 |
| 1024 | heft | 111.08 | 0.01 | 4.429 | 17 / 15–16 |
| 1024 | disagg fifo-hash | 136.27 | 0.01 | 10.737 | n/a |

Winner by intensity, with P3 (old kernel) for i1 and i16:

| iterations | winner | payload moved | runner-up |
|---|---|---|---|
| 1 (P3) | greedy-wait, keeps all local | 0 GB | stealing +4% |
| 16 (P3, old kernel) | greedy-wait | 0 GB | data-local +7% |
| 128 | beam-4 | 1.8 GB | data-local +2%, stealing +3%; all-local greedy-wait +40% |
| 1024 | data-local | 2.9 GB | greedy-wait +5%, beam-4 +7%, heft +13% |

- **Moving work off n0 pays from i128.** At i128 the three policies that move 1.4–1.8 GB
  (20–23 shards) off n0 beat the all-local greedy-wait by 26–28%; at i1024 every policy
  moves 2.6–4.4 GB, greedy-wait included. At i1 and i16 locality won (P3); i16 was measured
  with the old kernel, whose penalty on parallel compute favoured locality, so the full run
  must re-measure it.
- **HEFT still moves the most and is slowest** at i128 (3.9 GB, 55.7 s): its link model
  does not see that every transfer leaves through n0's one link. At i1024 compute dominates
  and the spread shrinks to 13%.
- **Beam-4 wins at i128 by 2%,** within single-repeat noise of data-local and stealing.
- greedy-wait planned for 20.7–23.6 s on 128 tasks, overlapped with execution.
- Speedup, cs best over disagg: 2.62x at i128, 1.38x at i1024.
- No OOM events in any container afterwards (`memory.events`); n0 reached its memory limit
  1,065 times (page-cache reclaim), with 0 `oom` and 0 `oom_kill`.

### Full runs, sized by the rules from the main assistant

| campaign | settings | campaign.py plan | estimate from measured costs |
|---|---|---|---|
| demo3-sweep | own 16 GiB dataset, 64 MiB, round-robin, ratio 0.25; i 1, 4, 16, 64, 256, 1024, 4096 (7 points to the largest piloted intensity, since the speedup never fell to 1.1x); 3 repeats; cs heft, data-local; disagg fifo-hash, heft; delete at end | 4.65 h | 5.49 h (under 7 h, so 16 GiB kept) |
| demo3-skew | 8 GiB, 65 shards on n0, 9 on each other node; i 1, 16, 128, 1024 (the winner changes at 128 and at 1024; moving work pays at both); 3 repeats; cs data-local, greedy-wait, stealing, heft, beam-4; disagg fifo-hash | 1.85 h | 2.20 h |
| demo3-droid | shards 0–549 at 128 px, 7 waves of 79 (65–75 GB each, 497.8 GB in all); cs data-local, beam-4; disagg heft; 1 pass; held-out `droid-128-02047` untouched | 10.47 h | 16.64 h |
| demo3-scale | 256 GiB independent, ratio 0.25; i1 and i256 (speedup 2.47x, nearest 2x); cs heft, data-local; disagg fifo-hash; 1 repeat; delete at end | 1.79 h | 3.89 h |
| **chain** | sweep, skew, DROID, scale | 18.8 h | **28.2 h** |

The measured-cost estimate is `demo3/chain_estimate.py` (output in
`pilots/chain-estimate.txt`): pilot walls scaled linearly with source bytes, log-log
interpolation between piloted intensities (skew i16 is interpolated between P3's i1 and
pilot2's i128), plus the final-output verification read at 50 MB/s of outputs, 2 s (synthetic)
or 17 s (DROID) per run, 7 s per mode switch, a full verify (200 s cs, 380 s disagg) at each
campaign start and every 6 h, dataset creation at 0.37 GB/s and DROID ingest at 132 MB/s.
`campaign.py plan` leaves out the verification read. No cut was needed: from the 03:14 EST
start the chain should end about Saturday 07:30 EST, 20 h before the Sunday 04:00 limit.
Unverified: beam-4 planning on a 79-shard DROID wave (about 9,300 tasks, against 472 in the
pilot); the estimate uses the pilot's 12.4 s per source GB.

Launched as runjob `demo3-campaigns` on aakashpc at 2026-11-13 03:14:02 EST
(`~/runs/demo3-campaigns-20261113-081401`), running `/mnt/mass/cephdag/tools/demo3-chain.sh`
(copy in `demo3/demo3-chain.sh`) from the clone at `2b22dd6`.

First steps against the estimate (ledger seconds, including mode switch and verify):

| step | measured s | estimate s | ratio |
|---|---|---|---|
| create (16 GiB) | 43.3 | 46 | 0.94 |
| independent-i1-r0-cs (with session verify) | 393.6 | 410 | 0.96 |
| independent-i4/16/64-r0-cs | 200.2 / 204.2 / 215.0 | 203 / 205 / 218 | 0.99 |
| independent-i256/1024/4096-r0-cs | 276.9 / 480.8 / 1267.7 | 263 / 461 / 1252 | 1.01–1.05 |
| independent-i1-r0-disagg (first disagg, with switch) | 322.6 | 330 | 0.98 |

At 16 GiB and i1: cs heft 10.86 s, data-local 10.89 s; disagg heft 70.10 s, fifo-hash 72.35 s
(6.45x); all four verified with digest `cabdafa6b05a`.
