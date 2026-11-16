# Demo 3 plan (Monday 2026-11-16, noon ET)

What Aakash asked for on 2026-11-12: interesting scheduler results across the full DROID
processing pipeline; synthetic tests at different arithmetic intensities; terabyte scale;
more computational storage nodes if reliability allows; a non-computational-storage
baseline that makes a fair comparison. Resources he allowed: the HDD on aakashpc for data
and tests, up to 500 GB of the SSD, restarts if needed.

## Decisions

Ceph OSDs live on the SSD, capped at 460 GiB in total. The HDD (sda, 14.6 TB ext4, label
MassStorage) is one spindle shared by every emulated node: about 200 MB/s for all of them
together, so roughly 30 MB/s each, below the 47 MB/s shaped network. Locality could not
help there, and the experiment would test the disk rather than computational storage. The
HDD holds the raw DROID shards, results, logs and archives under /mnt/mass/cephdag.

The baseline is a disaggregated mode of the same Ceph cluster, so both systems process
identical bytes: 8 storage-only nodes (OSD, no compute) plus 8 compute-only workers, against
8 computational-storage nodes (OSD plus a worker on the same node). Worker CPUs, worker
memory and per-NIC shaping (400 Mbit) are the same in both modes. In the disaggregated mode
every input and every intermediate crosses the network, as in a conventional compute
cluster over a storage cluster. Its scheduler is `fifo-hash`: ready tasks in order to free
workers, outputs placed by hash as CRUSH would; greedy and HEFT also run on it.

Data is stored once. Reliability comes from memory limits that leave the OSD room (the
2026-11-03 failure was ceph-osd killed in a 2.5 GiB container), checksums on every read,
and the acknowledgment protocol. Two copies would halve the dataset and add a network
write per output that the cost model does not charge yet.

Terabyte scale: about 450 GiB fits in Ceph at once, so 1 TB of DROID runs as waves of
about 100 GB of source. Each wave is ingested once and every configuration runs on it
before the next wave, which also pairs the comparisons against drift.

## Measured inputs to the budget

- Old lab, per node: local RADOS read about 500 MB/s, write about 470 MB/s; shaped network
  47 MB/s each way at 400 Mbit (baseline-20261103 models.json).
- DROID at 128x128 on JarvisVM: decode 31, encode 35, stats 19, parquet 0.4 CPU-s per
  source GB; frames are 2.16x the source.
- DROID 1.0.1: 2,048 shards, 1.866 TB, median 905 MB; GCS to aakashpc at 99 MB/s.
- Scheduler code before the scale-out work: HEFT first decision 97 s at 16k tasks; after,
  5.3 s at 65k tasks, and coordinator overhead 0.46 ms per decision at 65k.

## The story, and what each experiment has to show

From Aakash on 2026-11-12: prioritize the experiments that tell the best story, run smaller
tests first to get an idea, and use all the time through the weekend.

1. When does computing at the storage pay? Speedup of computational storage over the
   disaggregated baseline against arithmetic intensity, with the first-principles curve
   drawn on top: about 10x while both are I/O bound (8 x 500 MB/s local against 8 x 47 MB/s
   network), shrinking from about 30 iterations, near 1x above about 380. Experiment: the
   intensity sweep.
2. Does it hold on a real pipeline? DROID decode, encode, statistics and Parquet, where the
   frames intermediate (2.2x the source) is read twice: the baseline moves every byte of it
   across the network, computational storage keeps it local if the scheduler places it
   there. Wall time and bytes moved, CS against disaggregated, at 500 GB.
3. Once the computation sits at the storage, the scheduler decides where work and outputs
   go, and that matters most when data is unevenly spread. Skewed placement (half the data
   on one node): data-local leaves workers idle, HEFT and stealing move data, beam plans
   ahead at a cost. This connects to the headroom result of 2026-11-12 (idle workers and
   source-read movement are where the time goes) and to the original lookahead question.
4. Scale: the headline comparisons at 256 GiB synthetic and 500 GB DROID, with repeats,
   so that the numbers are not small-run artefacts.

## Order

1. Done 2026-11-12: code (scale-out, DROID port, N-node lab with the disaggregated mode,
   merged on demo3); HDD mounted; DROID download (554 shards, 501.5 GB, each file checked
   against its GCS MD5) finished 2026-11-12 ~20:43 EST, exit 0, at /mnt/mass/cephdag/droid/raw.
2. Build, verify and calibrate the cephdag8 lab; smoke tests in both modes.
3. Pilots, about 30 minutes each, one repeat, to size the effects before spending hours:
   sweep at 4 GiB (iterations 1, 32, 512, 2048); DROID on 2 shards in both modes; skew at
   8 GiB with half the data on node 0 at a middle intensity.
4. Full runs in the order the pilots justify, headline numbers with at least 3 repeats:
   probably the sweep (about 5 h), DROID (about 19 h), then skew and scale.
   - Done 2026-11-13 01:50–03:14 EST: chunked numeric kernel merged and deployed (cephdag
     `9ca8ce7`, then specs `2b22dd6`), recalibrated (v4), re-piloted sweep and skew
     (PILOTS.md, 'Re-pilots with the chunked kernel').
   - Launched 2026-11-13 03:14 EST as one chain, runjob `demo3-campaigns` on aakashpc
     (`/mnt/mass/cephdag/tools/demo3-chain.sh`), estimated end times from measured costs:

     | campaign | spec | est. h | est. end (EST) |
     |---|---|---|---|
     | sweep | `demo3-sweep.json`: 16 GiB, i 1–4096 (7 points), 3 repeats, cs heft/data-local, disagg fifo-hash/heft | 5.5 | Fri 08:45 |
     | skew | `demo3-skew.json`: 8 GiB half on n0, i 1/16/128/1024, 3 repeats, 5 cs policies, disagg fifo-hash | 2.2 | Fri 10:55 |
     | DROID | `demo3-droid.json`: shards 0–549, 7 waves of 79, cs data-local/beam-4, disagg heft | 16.6 | Sat 03:35 |
     | scale | `demo3-scale.json`: 256 GiB, i1 and i256, 1 repeat, cs heft/data-local, disagg fifo-hash | 3.9 | not in this chain: moved to stage B |
   - Stopped 2026-11-13 19:37 EST while still waiting, nothing deployed: runjob
     `demo3-followon` (`demo3-followon.sh`), which would have run sensitivity sweep, sensitivity
     DROID and overlap sweep after the whole chain, including scale. Replaced by the queue
     below, so the fairness checks come before scale.
   - Queue from 2026-11-13 19:37 EST:
     1. DROID, running in `demo3-campaigns` (wave 3 of waves 0–6 at 19:37 EST), expected to
        end about 04:00–05:00 EST Sat.
     2. Stage A, runjob `demo3-stage-a` on aakashpc (`/mnt/mass/cephdag/tools/demo3-stage-a.sh`,
        copy in this directory), started 19:37 EST. It holds
        `/mnt/mass/cephdag/campaigns/demo3-scale/driver.lock`, so the chain's `demo3-scale`
        stops with "another driver runs campaign demo3-scale" before touching the lab, and
        waits for the chain. It goes on only if `demo3-droid complete` and that lock refusal
        are both in the chain's log; on any other ending (failure during DROID, exit 0, lost
        chain, no finish by Sun 2026-11-15 12:00 EST) it exits 1 without touching the lab.
        Then, as the follow-on would have: clone to cephdag demo3 `bb7de4b`, deploy to all 17
        containers, derived models checked against SHA256SUMS, `verify cs`, the two campaigns
        below, `mode cs --functions cephdag.droid`. About 1 h after DROID ends.

        | campaign | spec | est. h (plan) |
        |---|---|---|
        | sensitivity sweep | `demo3-sensitivity-sweep.json`: 4 GiB, i1/256/4096, 2 repeats, cs heft, disagg fifo-hash, disagg2 fifo-hash | 0.49 (0.70) |
        | sensitivity DROID | `demo3-sensitivity-droid.json`: shards 550–552, unfused and fused, cs data-local, disagg heft+fifo-hash, disagg2 fifo-hash | 0.44 (0.38) |

     3. Stage B, runjob `demo3-stage-b` on aakashpc, started 2026-11-13 20:24 EST, waiting for
        stage A to exit 0 (`/mnt/mass/cephdag/tools/demo3-stage-b.sh`, copy
        `demo3/demo3-stage-b.sh`). Deploys cephdag `cbd1561` (branch `schedulers`, reviewed by
        Jarvis 2026-11-13), then sched-pilot, sched-skew, sched-uniform (new schedulers
        heft-cc, backlog, lp-plan; 0.37 + 2.75 + 1.18 h), `demo3-overlap-sweep` (3.41 h),
        `demo3-scale` (3.9 h): about 11.6 h, so it should end around Saturday 2026-11-14
        evening ET. See `SCHEDULERS.md`.
     4. Stage C, runjob `demo3-stage-c` on aakashpc, started 2026-11-14 08:06 EST, waiting
        (heartbeat every 240 s) for stage B to exit 0 with `stage B complete` in its log; on
        any other ending, or no finish by Sun 2026-11-15 08:00 EST, it exits 1 without
        touching the lab (`/mnt/mass/cephdag/tools/demo3-stage-c.sh`, copy
        `demo3/demo3-stage-c.sh`; gate tests `test_stage_c.sh`, 11 of 11 pass). Why: stage A
        compared cs (8 worker processes) with disagg2 (16); cs2 runs two workers per storage
        container with the same CPU quotas, so cs2 against disagg2 is slot-matched. It deploys
        cephdag `3bc0c28` (branch `cs2`), derives `/results/derived-models/cephdag8-cs2` (hash
        checked against the JarvisVM derivation), `verify cs`, then:

        | campaign | spec | est. h (plan) |
        |---|---|---|
        | slots DROID | `demo3-slots-droid.json`: shards 550–552, unfused and fused, cs data-local, cs2 data-local, disagg2 fifo-hash, 1 repeat | 0.28 (0.29) |
        | slots sweep | `demo3-slots.json`: 16 GiB of 64 MiB shards round-robin, independent, ratio 0.25, i1/64/256/1024/4096, 2 repeats but i4096 once, cs2 data-local+heft, disagg2 fifo-hash | 1.74 (1.49) |

        With both i4096 repeats the sweep estimated 2.28 h, over the 2 h budget, so the second
        i4096 repeat was dropped (spec key `once`). Estimates are upper bounds: cs2 and disagg2
        are priced at cs and disagg pilot rates. Expected start 15:30–17:00 EST Sat (when stage
        B ends), end about 17:30–19:20 EST Sat, leaving the lab in cs mode with DROID loaded.
     5. Stage D, runjob `demo3-stage-d` on aakashpc, started 2026-11-14 08:31 EST, waiting
        (heartbeat every 240 s) for stage C to exit 0 with `stage C complete` in its log; on
        any other ending, or no finish by Sun 2026-11-15 12:00 EST, it exits 1 without
        touching the lab (`/mnt/mass/cephdag/tools/demo3-stage-d.sh`, copy
        `demo3/demo3-stage-d.sh`; gate tests `test_stage_d.sh`, 11 of 11 pass). Why: the
        falsifiable test of DIAGNOSIS.md section 2. lp-plan2 is lp-plan with its two
        dispatcher changes ("wait for the link", "node takes back its own tasks"); the
        in-process forecast with the lab-like link gives 39.2 s at i128 and 91.6 s at i1024,
        against lp-plan 42.1/115.6 and heft-cc 37.6/95.1. If lp-plan2 does not bring i1024
        below about 105 s, the link-feeding explanation is wrong. It deploys cephdag `ac2f85d`
        (branch `lp-plan2`, from `cs2`; the -next repository fetched it from
        `/mnt/mass/cephdag/tools/cephdag-lp-plan2-ac2f85d.bundle`), checks the models against
        SHA256SUMS, `verify cs`, then:

        | campaign | spec | est. h (plan) |
        |---|---|---|
        | sched-lp | `sched-lp.json`: sched-skew's 8 GiB (65 of 128 shards on node 0), i128 and i1024, 3 repeats, cs lp-plan2, lp-plan, heft-cc, own dataset deleted at the end | 0.67 (0.61) |

        lp-plan2 is priced at lp-plan's measured sched-skew rate, lp-plan and heft-cc at their
        own. With the 3.5 min verify, deploy and final mode switch, about 0.75 h after stage C
        exits: expected start 17:30–19:25 EST Sat, end about 18:15–20:10 EST Sat, leaving the
        lab in cs mode with DROID loaded.
     6. Stage E, runjob `demo3-stage-e` on aakashpc, started 2026-11-14 14:29 EST, waiting
        (heartbeat every 240 s) for stage D to exit 0 with `stage D complete` in its log; on
        any other ending, or no finish by Sun 2026-11-15 14:00 EST, it exits 1 without
        touching the lab (`/mnt/mass/cephdag/tools/demo3-stage-e.sh`, copy
        `demo3/demo3-stage-e.sh`; gate tests `test_stage_e.sh`, 11 of 11 pass). Why: Aakash
        asked whether MCTS or learned schedulers were tried; native MCTS last ran on the old
        3-node lab (2026-11-03) and lost to HEFT and beam. This measures the existing native
        MCTS on cephdag8, with no new scheduling code. The in-process fair-sharing forecast
        (`sched_forecast.py`) gives native-mcts-heuristic-128 26.6 s / 4.06 GB moved at i1,
        49.5 s / 4.60 GB at i128, 95.5 s / 5.77 GB at i1024, against heft-cc 20.2/37.7/93.8 s
        and beam-4 31.3/39.2/103.7 s: MCTS behind heft-cc everywhere, and moving the most data.
        It deploys cephdag `8cdc2b4` (branch `mcts-lab`, from `lp-plan2`; the -next repository
        fetched it from `/mnt/mass/cephdag/tools/cephdag-mcts-lab-8cdc2b4.bundle`), checks the
        models against SHA256SUMS, `verify cs`, then:

        | campaign | spec | est. h (plan) |
        |---|---|---|
        | sched-mcts | `sched-mcts.json`: sched-skew's 8 GiB (65 of 128 shards on node 0), i1, i128 and i1024, 2 repeats, cs native-mcts-heuristic-128, heft-cc, beam-4, data-local, own dataset deleted at the end | 0.75 (0.70) |

        Native MCTS has no cephdag8 pilot and is priced at heft's sched-skew rate, above its
        forecast at every intensity; the others at their own. Checked before launch: MCTS runs
        to completion in-process on the skew i1 graph with the 8-node cs cluster file, both on
        JarvisVM and in a throwaway `--network none` container of the lab image (Python 3.9.25,
        the image's native library), with identical results. With deploy, verify and the final
        mode switch, about 0.85 h after stage D exits: expected start 18:20–20:15 EST Sat
        (about 19:20 if stage D ends at 19:15), end about 19:10–21:05 EST Sat (about 20:10),
        leaving the lab in cs mode with DROID loaded. The disk guard watches it too (restarted
        14:29 EST with `demo3-stage-e` in its list).
        Done: started 17:53, finished 18:37 EST Sat, exit 0, 24 rows verified; results in
        RESULTS-mcts.md. Lab left in cs mode with DROID loaded; 321 GiB free on aakashpc's root.
6. Analysis and figures Sunday; slides only if Aakash asks for them; Monday morning is buffer.

## Disk on aakashpc

The NVMe root had 1.1 TiB free before the lab, 1017 GiB after validation, 582 GiB through the
DROID campaign (flat from Friday afternoon to Saturday morning while DROID rewrote terabytes), and
411 GiB at 14:15 EST Saturday during demo3-scale. So the lab's footprint is about 700 GiB, not the
448 GiB of OSD block files, and over the 500 GB Aakash allowed. Host space appears to track the
high-water mark of Ceph's allocations (the scale campaign's 339 GiB resident set is the largest so
far) and does not come back when Ceph deletes, because BlueStore discard is off and the block files
sit on copy-on-write btrfs; the excess over 448 GiB is probably btrfs extent overhead, not verified
(needs root). A guard, runjob `demo3-disk-guard` (`/mnt/mass/cephdag/tools/disk-guard.sh 300`,
copy `demo3/disk-guard.sh`), checks every minute and stops stages B to E and the coordinator
in cephdag8-n0 if free space falls below 300 GiB. Aakash, 2026-11-14: keep running, and clean up
after Monday (remove the cephdag8 OSD volumes once the demo is done).

Correction, 18:15 EST Saturday: the high-water-mark explanation above is wrong. After the scale
dataset was deleted Ceph held only 45 GiB, yet free space kept falling, from 411 GiB at 14:15 to
329 GiB at 18:13, about 0.9 GiB a minute while stage E wrote 8 GiB graphs. So the block files'
footprint on btrfs grows with every write, whatever Ceph currently holds, and nothing comes back
until the volumes are removed. Why btrfs does this here (copy-on-write extents, compression,
snapshots: `/.snapshots` is not readable without root; snapper-cleanup.timer runs hourly) is not
established.

19:00 EST Saturday, with his OK to clean up what is no longer needed: removed the old 3-node lab
(containers cephdag-n0..n2 and cephdag-router, volumes cephdag_osd0..2 of 51.5 GB each, networks
cephdag_link0..2, image cephdag-lab:20.2.4-amd64-baseline20261103). Free space went from 315 to
319 GiB, not up by about 155 GB. `/var/lib/docker` sits in the btrfs subvolume `@` (no separate
mount), which snapper snapshots, so the likeliest explanation for both observations is that
snapshots still reference the deleted and overwritten extents; the space should return as snapper
expires them, or at once with `sudo snapper delete`. Not verified (needs root).

## Network-speed sweep (runjob demo3-netsweep, started 18:58 EST Saturday)

Question: how much of the speedup is the 400 Mbit network? cs2 heft against disagg2 fifo-hash
(16 worker processes each), 8 GiB of 64 MiB shards round-robin, i1 and i256, 2 repeats, with all
links shaped to 1000, then 2500, then 400 Mbit in one session (`demo3/netsweep.sh`, specs
`cephdag/experiments/campaigns/netsweep-*.json`, branch `netsweep` 29f8b37; no code change, the
deployed code is mcts-lab 8cdc2b4). Bandwidth checks before launch, 8 concurrent ring flows: 943
Mbit per node at 1000, 2357 at 2500, so the router carries both. Prediction: the baseline's
transfer time scales with 1/rate, so at i1 the speedup falls from about 5.6x toward 2.5x and
1.4x; at i256 compute dominates and it changes less. Estimate 0.86 h plus switches; the disk
guard runs with a 220 GiB floor (Ceph's mon stops at 5% free, about 186 GiB).
