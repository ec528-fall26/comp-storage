# cephdag8 lab validation on aakashpc (2026-11-12, after the evening reboot)

The lab has three parts:
- 8 storage nodes, `cephdag8-n0..n7`, with one OSD each;
- 8 compute-only nodes, `cephdag8-c0..c7`;
- `cephdag8-router`.

It runs Ceph 20.2.4 from `~/projects/comp-storage/cephdag`, branch `demo3`.

The approval is recorded in `~/experiments/cephdag-demo3/state/cephdag8/approval.json` (Aakash, 2026-11-12):
"you can use the hard drive on aakashpc, and keep all your tests there. Make sure you have a
baseline non computational storage system as well that makes sense to compare against computational
storage. You can restart if needed. You can use up to 500 gb of the ssd if needed."

**State left:**
- The lab is running in **cs mode**. All 8 storage workers answer `/state`; they were started with `--functions cephdag.droid`.
- Every node `eth0` and all 16 router ports run `tbf rate 400Mbit burst 128Kb lat 400ms`. Nodes route through the router over IPIP.
- Ceph: 8/8 OSDs are up and in, and all 257 PGs are active+clean.
- The compute containers are stopped, which is expected in cs mode.
- The calibrations are at the paths the campaign specs use.
- The weekend campaign was **not** launched.

## Kernel and reboot

Before the reboot, aakashpc ran `7.3.1-1-cachyos` with its module tree gone, so `ipip` and `sch_tbf` could
not load. A first validation ran with in-container fallbacks: direct routing and nftables policing. Its results
are kept only as code-path evidence, in `/mnt/mass/cephdag/deploy/police-attempt/` and
`demo3/lab-validation/police-attempt/`. They are not measurements on the lab's network model.

Policing drops packets where tbf queues them, and it changed the disagg timings substantially:

| disagg run | policed | tbf |
|---|---|---|
| fifo-hash, 1 iteration | 4.9 s | 9.5 s |
| DROID | 63 s | 104 s |

As instructed, the fallbacks were reverted and never became the default.

aakashpc came back at about 22:30 ET. Checks after the reboot:
- `uname -r` reported `7.3.3-1-cachyos`.
- `/mnt/mass` was mounted (`/dev/sda`, ext4).
- Inside a container, both `tc qdisc add … tbf` and `ip tunnel add … ipip` worked, and `ipip` and `sch_tbf` are loaded.
- `lab.py resume` took 19 s, `shape 400` 2 s and `mode cs` 12 s.
- Ceph came back with 8/8 OSDs up and 257 PGs active+clean.

This was a real host reboot, so it doubles as the restart test. Everything below was measured after the reboot.

## Deployed code

- The aakashpc clone `~/experiments/cephdag-demo3` is at **17ad72e**, with a clean worktree. 191/191 tests
  pass with `.venv/bin/python -m unittest discover -s tests -t .` on the VM.
- The image `cephdag-lab:20.2.4-amd64-scale20261112` (`sha256:0ae1f3baeabc…`) was built from **8b6c6a9** before
  the reboot, and all 17 containers run it.
  - Its runtime code (`cephdag/`, `experiments/`, `native/`) is identical to 17ad72e.
  - It differs in two ways. Its `infra/network.py` still has the nftables fallback, which runs only when
    `tc … tbf` fails, and that does not happen on this kernel. It also carries the `nftables` package.
  - `lab.py shape` runs on every campaign mode switch. It calls `tc … tbf` from the host side, so on a
    kernel without tbf it would fail loudly instead of falling back.
  - I did not rebuild the image, because that means recreating the containers. A rebuild before the campaign is optional.
- Commits on `demo3` since 0006db6:

```
17ad72e verify: rate each iperf3 flow at its sender; behind tbf a receiver often closes its summary ~10 s late …
ae60b02 verify: run iperf3 servers as detached docker exec processes and wait until they listen; iperf3 -D left zombies …
26fbb13 Revert "Network: on a kernel without IPIP, route each node through the router's address …"
469cb64 Revert "Network: a container starts unshaped, saying so, where the kernel cannot load sch_tbf …"
7b21e32 Revert "Network: where the kernel cannot load sch_tbf, police each link with an nftables egress limit …"
8b6c6a9 Network: … nftables egress limit …           (reverted)
646ef05 mode disagg: a restarted container is ready when PID 1 ends with 'sleep infinity' …
8b9aa4f Network: a container starts unshaped …       (reverted)
29bf9bc Network: on a kernel without IPIP, route …    (reverted)
e4fe372 (parent) Split the synthetic campaign: demo3-sweep.json and demo3-scale.json
76e8196 Image: build the Rust planner in a Ceph-image stage with the pinned official Rust 1.85.0 …
```

The net change since 0006db6, besides the parent's e4fe372:
- **76e8196**: The Ceph image now builds the Rust planner with official Rust 1.85.0, hash-pinned in
  `infra/rust-toolchain.sha256`; the archives are in `deps/rust/`, which is gitignored. In the image,
  `planner_abi_version()` returns 5, so beam-4 runs in the coordinator.
- **646ef05**: `mode disagg` never saw a compute container as ready. In the Ceph image, PID 1 is
  `/usr/bin/coreutils --coreutils-prog-shebang=sleep /usr/bin/sleep infinity`, not plain `sleep infinity`.
- **ae60b02**: Every verify used to leave 2 zombies per node, and zombies count against the pids limit of 512.
  - Cause: `iperf3 -D` reparented the servers to PID 1 (`sleep`), which never reaps them.
  - Now verify leaves 0 zombies per node.
  - n0 still holds 2 zombies from a manual test of mine. They go away when that container next restarts.
- **17ad72e**: The verify gate now uses each flow's sender rate.
  - Why: behind tbf, one receiver of a pair often closes its iperf summary about 10 s after its sender stops.
    For example, at n0 in cs the receivers reported 46 + 242 Mbit, while the eth0 byte counters showed 378.
  - Before this change, `verify cs` failed at nodes 2, 3, 5 and 6.
  - Cross-check: the lab's `--probe boundary-window` reads NIC byte counters over 10 s. It gave 377.8–377.9 Mbit
    on all 16 node×direction windows in cs. The sender rates were 376.8–380.2.

## Acceptance

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | 17 containers, limits, Ceph | **pass**. All 17 containers were up in disagg mode, and verify disagg covered all 16 nodes. Limits: n0–n7 have 3 CPU and 6144 MiB; c0–c7 have 2 CPU and 3072 MiB; the router has 0.25 CPU and 128 MiB. Swap equals memory, pids are capped at 512, and only `CAP_NET_ADMIN` is added (nothing privileged). Ceph: 8 OSDs up and in, 257 PGs active+clean, every pool `nodeI` at size 1 / min_size 1. `verify` confirmed that all 32 PGs and 3 objects of each pool map to OSD I only. Health is HEALTH_WARN with 4 benign warnings (below) | `results/deployment/cephdag8/ceph-*.json`, `docker inspect` |
| 2 | verify both modes | **pass**. Routes go over IPIP via `10.77.N.2 dev lab`, and tbf is set on every node and router port. RADOS writes and checksummed read-back cover all 8 pools: from n0 in cs, and from n0 plus all 8 compute nodes in disagg. Shared rates are in the table below | `/mnt/mass/cephdag/deploy/validated-{cs,disagg}.json`, `boundary-window-cs-400.json` |
| 3 | mode switches | **pass**. `mode cs` took 12–13 s and `mode disagg` 4 s; repeating either took 0–1 s and started or stopped nothing. After `mode disagg`, c3's `eth0` has `tbf rate 400Mbit burst 128Kb lat 400ms`. Stop and resume were tested twice: by the host reboot followed by `resume` (above), and before the reboot with `stop-workers` (1 s), `stop` (12 s) and `resume` (21 s) | `deploy/logs/validate-tbf/*.log`, `deploy/logs/reboot/` |
| 4 | calibration | **pass** in both modes, at the spec paths (numbers below) | `/mnt/mass/cephdag/calibration/cephdag8-{cs,disagg}/{models,calibration}.json` |
| 5 | smoke runs | **pass**. All 14 runs have `verification.passed`; synthetic runs were checked against the independent oracle, DROID runs against the DROID verifier. For each workload, the output digests are identical across policies *and* across modes | table below; `/mnt/mass/cephdag/smoke/validate/*` |
| 6 | campaign dry run | **pass**. 4/4 steps ran in 680 s. A rerun printed `dryrun-tiny: 4 of 4 done` in 0 s. The smoke datasets were then deleted | `/mnt/mass/cephdag/campaigns/dryrun-tiny/` |
| 7 | memory | **pass** at smoke scale: no OOM events in any container. Peaks are below | `deploy/memory-after-smoke.txt` |

Shared bandwidth: each node in turn sends two flows to its ring successors at once, then receives two. Values are Mbit/s, and the gate is 70–120 % of 400.

| mode | shared TX | shared RX | verify time |
|---|---|---|---|
| cs (8 nodes) | 376.8–377.9 | 377.5–380.2 | 182 s |
| disagg (16 nodes) | 376.8–378.0 | 377.5–379.6 | 380 s |
| cs, boundary-window probe | 377.8–377.9 | 377.8–377.9 | 6 min |

tbf at 400 Mbit yields 378 Mbit of TCP goodput (94.4 %). Earlier labs used the same `tbf … 400mbit` shaping.

Router headroom: in disagg, 8 simultaneous `c_i → n_i` flows each got 375–376 Mbit, a **3005 Mbit aggregate**
through the router. Over those 10 s the router cgroup used 12 ms of CPU and was not throttled.

The 4 Ceph warnings are all expected for this lab:
- `AUTH_INSECURE_GLOBAL_ID_RECLAIM_ALLOWED` is a lab-internal mon setting.
- `OSDMAP_FLAGS noscrub,nodeep-scrub` is set by bootstrap so that scrubs do not disturb timings.
- `POOL_NO_REDUNDANCY` reflects 1 replica by design.
- `MON_DISK_LOW`: the mon store sits on the NVMe root, which has 27 % free, below the 30 % warning level.
  Even with all OSDs full, about 600 GB would remain free, far above the 5 % critical level.

## Calibration

The network rate given to calibration is the weakest verified shared rate: 376.7 Mbit, or 47.09 MB/s per NIC.

| | cs (`8af0aa9f…`, 75 s) | disagg (`87b31a04…`, 273 s) |
|---|---|---|
| read MB/s, n0..n7 (local RADOS reads, measured in cs) | 612, 814, 892, 861, 803, 899, 880, 818 | same values, taken from cs via `--storage-from`; compute-only nodes get the median 839, marked "unused: compute-only" |
| write MB/s, n0..n7 | 494, 599, 462, 446, 558, 526, 618, 580 | same values; placeholder 542 |
| network MB/s per NIC | 47.09 (from verify) | 47.09 |
| compute: fixed; per input byte; per byte per iteration; per output byte | 0.50 ms; 1.73e-10 s/B; 5.36e-11 s/B; 5.78e-11 s/B (RMSE 1.1 ms) | 1.38 ms; 1.17e-10; 5.59e-11; 5.95e-11 (RMSE 1.7 ms) |
| DROID CPU-s per source GB, held-out shard 02047 (926.7 MB source, 120 samples) | decode_resize 16.06, encode_video 22.78, frame_stats 11.20, state_parquet 0.40; **total 50.4** | 16.26, 22.75, 11.16, 0.36; **total 50.5** |

`demo3-droid.json` assumes 85.4 CPU-s per source GB, so with the measured 50.4 its estimate is conservative.

## Smoke runs

cs runs use the storage workers and disagg runs use the compute-only workers. "Worker NIC" means the interfaces of that mode's workers.

Workloads:
- Synthetic: `smoke-2g-64m`, 2 GiB as 32 × 64 MiB shards on n0..n7; independent graph, ratio 0.25, seed 9103.
- DROID: shards 00000–00001 at 128 px, 272 tasks, seed 9203.

| run | mode | policy | iterations | tasks | wall s | policy s | payload over network GB | worker NIC rx GB | verified | outputs sha256 |
|---|---|---|---|---|---|---|---|---|---|---|
| cs-i1 | cs | data-local | 1 | 32 | 1.53 | 0.004 | 0.000 | 0.004 | True | 0f318e55a107 |
| cs-i1 | cs | heft | 1 | 32 | 1.60 | 0.003 | 0.000 | 0.004 | True | 0f318e55a107 |
| cs-i1 | cs | beam-4 | 1 | 32 | 1.57 | 0.104 | 0.000 | 0.003 | True | 0f318e55a107 |
| cs-i128 | cs | data-local | 128 | 32 | 20.58 | 0.005 | 0.201 | 0.243 | True | ac306eca697e |
| cs-i128 | cs | heft | 128 | 32 | 20.32 | 0.004 | 0.000 | 0.044 | True | ac306eca697e |
| cs-i128 | cs | beam-4 | 128 | 32 | 19.86 | 0.227 | 0.000 | 0.044 | True | ac306eca697e |
| cs-i2048 | cs | beam-4 | 2048 | 32 | 254.79 | 0.211 | 0.067 | 0.617 | True | 648be59703b4 |
| disagg-i1 | disagg | fifo-hash | 1 | 32 | 9.52 | 0.001 | 2.684 | 2.155 | True | 0f318e55a107 |
| disagg-i1 | disagg | heft | 1 | 32 | 16.28 | 0.003 | 2.684 | 2.154 | True | 0f318e55a107 |
| disagg-i128 | disagg | fifo-hash | 128 | 32 | 22.69 | 0.002 | 2.684 | 2.166 | True | ac306eca697e |
| disagg-i128 | disagg | heft | 128 | 32 | 21.35 | 0.004 | 2.684 | 2.160 | True | ac306eca697e |
| cs-droid | cs | greedy | DROID | 272 | 72.56 | 60.081 | 0.812 | 0.845 | True | 041002e05056 |
| cs-droid | cs | beam-4 | DROID | 272 | 31.12 | 1.510 | 0.358 | 0.425 | True | 041002e05056 |
| disagg-droid | disagg | fifo-hash | DROID | 272 | 103.80 | 0.009 | 17.415 | 11.382 | True | 041002e05056 |

Network sanity checks:
- disagg moves every input byte over the network. 2 GiB in plus 0.5 GiB out is 2.684 GB of payload, and the workers receive 2.16 GB.
- cs heft and beam-4 move no payload, apart from 0.067 GB at i2048. Their NIC bytes are control traffic.
- cs data-local moved 3 shards at i128, 0.20 GB.

## Campaign dry run

The spec is `/mnt/mass/cephdag/deploy/dryrun-tiny.json`. It is `demo3-sweep.json` with these changes:
- dataset `dryrun-tiny-4g-64m`, 4 GiB resident;
- one independent 2 GiB workload at 1 iteration;
- 1 repeat;
- `delete_at_end: true`.

`campaign.py plan` estimated 0.26 h.

`run` took 680 s in total:

| step | seconds | includes |
|---|---|---|
| create | 13.9 | |
| `independent-i1-r0-cs` | 226.9 | mode, shape, `verify cs`, 3 policies |
| `independent-i1-r0-disagg` | 425.7 | |
| delete | 0.5 | |

A rerun printed `dryrun-tiny: 4 of 4 done` and executed nothing.

Files it writes:
- `campaigns/dryrun-tiny/{ledger.jsonl, driver.log, driver.lock}`, owned by aakash;
- `<step>/a1/{config.json, rows.jsonl, run-NNN-<policy>.json}`, owned by root because the coordinator container writes them.

The dry-run results match the smoke runs: cs took 1.5–2.9 s; disagg fifo-hash took 9.91 s and heft 17.38 s; both modes produced digest `0f318e55a107`.

Afterwards I deleted `smoke-2g-64m` (32 objects), `droid-128-00000` (34) and `droid-128-00001` (34). The campaign deleted its own dataset.

## Memory

Peaks are cgroup `memory.peak` since the reboot. That window covers verify, ingest, calibration, the smoke runs and the dry run.

| container | peak MiB | limit MiB | notes |
|---|---|---|---|
| n0 (OSD + mon + mgr + coordinator + worker) | 3679 | 6144 | at the end, anon 448 MiB and page cache 2.3 GiB (reclaimable) |
| n1–n7 (OSD + worker) | 859–1046 | 6144 | ceph-osd RSS 149–233 MiB; `osd_memory_target` 2.4 GiB, autotune off; mempools 9–73 MiB |
| c0–c7 (worker) | 524–660 | 3072 | worker RSS 327–471 MiB |
| router | 21 | 128 | |

No container recorded an `oom` or `oom_kill` event. Each OSD holds at most 4.2 GiB of data so far, so OSD memory at campaign scale is unproven.

## Disk

- NVMe `/`: 1.1 TiB was free before (per the task brief). Afterwards **1017 G is free** (73 % used).
- `cephdag8_osd0..7`: each volume is a sparse 56 GiB block file, 57 GiB apparent, so 448 GiB logical in total.
  Actual use is **3.1–4.2 GiB each**, about 28 GiB in total. Ceph reports 448 GiB raw, 5.4 GiB used.
- RADOS holds 4.82 GiB in total:
  - `droid-128-02047`, the held-out calibration shard (884 MiB), kept on purpose;
  - sample objects of the two current calibrations (`8af0…`, `87b3…`), 1 GiB each;
  - about 2 GiB of sample objects from the two police-attempt calibrations (`runs/` and `calibration/` under `f9fe5341…` and `719d6575…`), which can be removed;
  - KB-sized run records.
- `/mnt/mass`: 468 G used. The DROID download finished with exit 0. `/mnt/mass/cephdag/{deploy,smoke,calibration,campaigns}` hold under 20 MB.
- The old lab is untouched. `cephdag-n0..n2` and `cephdag-router` still show `Exited (255) 6 days ago`, and the volumes `cephdag_osd0..2` are present. `gbrain-embedding` is up.

## Restart after a reboot

```
ssh aakashpc
uname -r; ls /usr/lib/modules/$(uname -r) >/dev/null   # the running kernel must have its modules
findmnt /mnt/mass
cd ~/experiments/cephdag-demo3
python3 infra/lab.py --lab infra/labs/cephdag8.json resume
python3 infra/lab.py --lab infra/labs/cephdag8.json shape 400
python3 infra/lab.py --lab infra/labs/cephdag8.json mode cs --functions cephdag.droid
python3 infra/lab.py --lab infra/labs/cephdag8.json verify cs
python3 infra/lab.py --lab infra/labs/cephdag8.json mode disagg --functions cephdag.droid
python3 infra/lab.py --lab infra/labs/cephdag8.json verify disagg
python3 infra/lab.py --lab infra/labs/cephdag8.json mode cs --functions cephdag.droid
```

On CachyOS, a kernel package upgrade removes the running kernel's modules. After such an upgrade, reboot before using the lab.

## Findings the campaign should know about

These are not fixed, because they are policy or method questions.

1. **The compute model under-predicts 64 MiB tasks.** Median of actual/model compute-stage time per run:

   | mode | i1 | i128 | i2048 |
   |---|---|---|---|
   | cs | 2.7–3.9× | 6.2–8.7× | 6.8× |
   | disagg | 1.2× | 5.3–7.7× | |

   - Calibration samples are 4 and 16 MiB (`SAMPLE_MIB` in `experiments/calibrate.py`) and run one at a time.
     The sweep streams 64 MiB per task on 8 workers at once.
   - The likely cause is cache plus memory bandwidth: 16 MiB fits in the 9950X3D's L3.
   - HEFT and beam plan with these models. Calibrating at the shard size, on all workers at once, would likely fix it.
2. **disagg HEFT puts every output in pool `node0`.** Storage-node payload RX is 512 MiB on n0 and 0 on every other node.
   - n0's 378 Mbit link therefore bounds the run: HEFT takes 16.3 s against 9.5 s for fifo-hash at i1.
   - Cause: all remote targets tie, and the tie goes to node 0.
   - This handicaps the disagg-HEFT baseline.
3. **Greedy spends 60.1 s of a 72.6 s DROID run planning** (272 tasks); beam-4 spends 1.5 s.
   `demo3-droid.json` runs greedy in cs over shards 0–549, so expect planning time to dominate those runs.
4. **Mode switches cost more than the campaign estimates.** Mode, shape and verify together take about 196 s for cs and 385 s
   for disagg, about 600 s per case pair. `demo3-sweep.json` assumes `switch_seconds: 240` per switch.
5. **Control traffic shows up as NIC bytes.** The coordinator polls `/state` every 5 ms. A run with almost no payload
   still shows NIC traffic, e.g. cs-i2048: 0.617 GB over 255 s. `network_payload_bytes` excludes it.
6. **Container-written results are root-owned on the host.** Move or delete them with `docker exec cephdag8-n0 …` on `/results/…`.
7. **`stop` is a plain `docker stop`.** PID 1 ignores SIGTERM, so every daemon gets SIGKILL after 10 s.
   Ceph came back clean after both a `stop`/`resume` and the host reboot. A graceful daemon stop would be safer.

## Not proven

- OSD memory and nearfull behaviour at the 256 GiB scale.
- `stop` during an active run; it was only tested idle.
- Compute-model accuracy at campaign scale (see finding 1).

## Files

On the VM:
- this file;
- `demo3/lab-validation/`, which holds:
  - `validate-tbf.sh` (the exact post-reboot sequence) and its output, `validate-tbf-output.log`;
  - `dryrun-tiny.json`;
  - helper scripts: `resume.sh`, `aggregate.sh`, `evidence2.sh`, `mem.sh`, `fits.py`, `smoke_table.py`, `pool_usage.py`;
  - evidence: `validated-{cs,disagg}.json`, `boundary-window-cs-400.json`, `smoke-table-tbf.md`;
  - `police-attempt/`, the pre-reboot attempt.

On aakashpc:
- `/mnt/mass/cephdag/deploy/`: logs, `validated-*.json`, `bandwidth-*.json`, memory and Ceph snapshots, `image-commit.txt`, `police-attempt/`;
- runjob `~/runs/cephdag8-validate-tbf2-20261113-035007` (exit 0);
- results: `/mnt/mass/cephdag/calibration/cephdag8-{cs,disagg}/`, `/mnt/mass/cephdag/smoke/validate/`, `/mnt/mass/cephdag/campaigns/dryrun-tiny/`;
- lab evidence: `~/experiments/cephdag-demo3/results/deployment/cephdag8/`.
