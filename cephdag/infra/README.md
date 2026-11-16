# The lab

`infra/lab.py` creates, checks and stops one lab on one Docker host. A lab has S storage
nodes and C compute-only nodes, each a container with its own link to one router container
that shapes every link to the same rate. Storage node I (container `<name>-n<I>`) runs one
Ceph OSD, holds pool `node<I>` and can run a cephdag worker. Compute-only node J (container
`<name>-c<J>`, node number S+J) holds no OSD and no pool; it has the Ceph client
configuration and keyring and can run a worker. Node 0 also runs the Ceph monitor and
manager, and the coordinator and experiments run in `<name>-n0` from `/app`. Everything
about a lab comes from one file in `infra/labs/`.

## Two modes on one cluster

The same Ceph cluster and the same data serve two ways of computing:

| Mode | Running containers | Workers | Cluster file |
| --- | --- | --- | --- |
| `cs` (computational storage) | storage nodes, router | in the storage nodes | `cephdag/clusters/<name>-cs.json` |
| `disagg` (disaggregated) | all nodes, router | in the compute-only nodes | `cephdag/clusters/<name>-disagg.json` |

The cs file lists the S storage nodes, each `{"pool": "node<I>", "worker": url}`. The disagg
file lists the S storage nodes as `{"pool": "node<I>", "worker": null}` followed by the C
compute-only nodes as `{"pool": null, "worker": url}`, so node numbers are the same in both
files and in `--node` of each worker. Both name `node0` as the metadata pool.

Resources are split so that the two modes are comparable. A storage node's CPU quota and
memory limit are an OSD share plus a worker share; a compute-only node gets the worker share
alone. In cs mode a worker shares its container with its OSD; in disagg mode the storage
nodes' worker share is idle and the same work runs in the compute-only containers. Every link
has the same rate, so the storage tier's total egress is the same in both modes. In disagg
mode a storage node keeps its whole quota, so its OSD (and on node 0 the monitor, manager and
coordinator) could use more CPU than in cs mode; at the shaped rates an OSD needs far less.

## Lab configs

| Field | Meaning |
| --- | --- |
| `name` | Prefix of every resource: containers `<name>-n<I>`, `<name>-c<J>` and `<name>-router`, volumes `<name>_osd<I>`, networks `<name>_link<K>` (K = node number), and the state directory `state/<name>/` |
| `image` | Image tag that `build` creates and `up` runs |
| `storage_nodes` | S, from 3 (verify loads each node with two others) |
| `compute_nodes` | C, from 0; a lab with none has only cs mode. S + C is at most 256 |
| `osd_cpus`, `worker_cpus` | CPU shares: a storage node's Docker CPU quota is their sum, a compute-only node's is `worker_cpus` |
| `osd_memory_mib`, `worker_memory_mib` | Memory shares, combined the same way into the memory limit (no swap). `osd_memory_target` is 80% of the OSD share |
| `osd_gib` | Logical size of each storage node's BlueStore block file; also its CRUSH weight |
| `worker_slots` | Tasks a worker runs at once; only 1 exists |
| `first_subnet` | Node K gets the K-th /24 after it: node at `.10`, router at `.254` |
| `bandwidth_mbit` | Rate every link is shaped to whenever a container starts |
| `replicas` | 1: pool `node<I>` lives only on node I. 2: a second copy on storage node I+1 (see below) |
| `results_dir` | Host directory mounted read-write at `/results` in every node container; default `/mnt/mass/cephdag`; `null` mounts nothing |

| Config | Storage nodes | Compute-only nodes | OSDs | Copies | Subnets |
| --- | --- | --- | --- | --- | --- |
| `cephdag.json` | 3 x (1 + 1 CPUs, 1.25 + 1.25 GiB) | none | 48 GiB | 1 | 172.30.80-82 (the existing lab) |
| `cephdag8.json` | 8 x (1 + 2 CPUs, 3 + 3 GiB) | 8 x (2 CPUs, 3 GiB) | 56 GiB | 1 | 172.30.144-159 |

`cephdag.json` describes the lab created on 2026-11-03, whose nodes had 2 CPUs and 2.5 GiB
each, written as even OSD and worker shares, and no results mount. For it, the lifecycle
issues exactly the commands of the original three-node `lab.py` and `network.py`, apart from
one read-only `docker info` for the budget; `tests/test_infra.py` checks this against a trace
recorded from the original code (`tests/fixtures/make_lab_fixture.py`). Its Compose project
differs from the original only by the `SUBNETS` variable that `network.py` reads, and its cs
cluster file equals `cephdag/lab-cluster.json`. Other labs use different names and subnets,
so they can exist next to it; `--lab` is required, so nothing acts on a lab by default.

## Commands

Run from the repository root on the Docker host, with any Python 3.9 or newer, as
`python3 infra/lab.py` or `python3 -m infra.lab`:

```sh
L="--lab infra/labs/cephdag8.json"
python3 infra/lab.py $L plan             # dry run and budget, both modes; no Docker
python3 infra/lab.py $L cluster          # (re)write both cluster files
python3 infra/lab.py $L build
python3 infra/lab.py $L up
python3 infra/lab.py $L bootstrap
python3 infra/lab.py $L verify disagg    # every node running after up; --mbit R, --probe boundary-window
python3 infra/lab.py $L mode cs          # --functions module ...
python3 infra/lab.py $L verify cs
python3 infra/lab.py $L mode disagg
python3 infra/lab.py $L shape 250        # every running link and router port; remembered
python3 infra/lab.py $L stop-workers
python3 infra/lab.py $L stop
python3 infra/lab.py $L resume
```

`mode cs` stops the compute-only containers and starts any storage-node worker that is not
running; `mode disagg` stops the storage nodes' workers, starts the compute-only containers
(waiting until their links are set up and shaping them to the lab's current rate) and starts
their workers. Both then wait until every worker of the mode answers `POST /state` with
`{"keys": []}` from `<name>-n0`, where the coordinator runs, and fail after 120 s otherwise.
Running either again changes nothing. Switch modes only between runs: a worker stopped during
a task leaves a running-job record that needs manual recovery. `stop-workers` stops every
worker and the compute-only containers and leaves Ceph running.

`shape` sets the rate of every running node's link and every router port, and records it in
`state/<name>/shaped.json`; containers shape themselves to `bandwidth_mbit` when they start,
so `mode disagg` and `resume` apply the recorded rate to the containers they start. Without a
rate, `shape` reapplies the recorded one; `verify` checks against it unless given `--mbit`.

`build`, `up`, `bootstrap`, `resume`, `mode`, `stop-workers` and `shape` refuse to run until
`state/<name>/approval.json` records `{"approved": true}`; the approval of one lab does not
cover another. `up` and `bootstrap` refuse unless `results_dir` is an existing directory this
user can write. `up` refuses if any container, volume or network of the lab already exists,
if a lab subnet overlaps any Docker network, or if the lab does not fit the host (below).
`bootstrap` refuses non-empty storage volumes and runs once; it writes `ceph.conf` and the
admin keyring into every node and creates OSDs and pools on the storage nodes only. Every
later step checks that each container carries the lab's ownership label. Containers get only
`NET_ADMIN`, internal networks, no published ports, no host devices and no privileges; the
only host path they see is `results_dir`.

`verify cs` checks the storage nodes and `verify disagg` all nodes; it refuses if a node of
the mode is stopped. For each node: its interfaces, and that every other node is reached
through its tunnel to the router. For each storage node: its pool's size and `min_size`, and
that three object names and all 32 placement groups of pool `node<I>` have exactly the
expected up and acting OSDs with node I as primary. A RADOS write and checksummed read-back
of every pool from node 0 and, in disagg mode, from every compute-only node. Then each node
of the mode in turn sends two iperf3 flows at once to its two ring successors (shared TX) and
receives two from them (shared RX); every node's two sums must be 70-120% of the rate. That is
4 flows of five seconds per node, about 15 s per node. `validated-<mode>.json` records all
per-node rates and, as `aggregate_mbit`, the weakest node's, which
`experiments/run.py --validation` uses as the network rate.

## Two replicas

With `replicas: 2`, pool `node<I>` uses CRUSH rule `pin<I>` (I+1 wraps around the storage nodes):

```
rule pin<I> {
	id <I+1>
	type replicated
	step take n<I>
	step choose firstn 1 type osd
	step emit
	step take n<I+1 mod S>
	step choose firstn 1 type osd
	step emit
}
```

The first OSD emitted is the primary, so node I still serves every read of pool `node<I>`,
as the scheduler assumes. `ceph osd crush rule create-replicated` takes one bucket, so
bootstrap adds these rules by decompiling the CRUSH map, appending them and setting it
back. The second copy always goes to the next node, never to a random one, so placement is
predictable and `verify` can require it exactly.

`size` is 2 and `min_size` is 1. In normal operation this changes nothing: Ceph
acknowledges a write only after both copies are written. `min_size` matters only while
one of the two OSDs is down. With 1, pools `node<I-1>` and `node<I>` stay readable and
writable on their surviving copy, so a run survives one OSD crash (such as the
out-of-memory kill of 2026-11-03), and the copy is brought back up to date when the OSD
returns. The price: writes made during that window exist once until recovery ends, and if
the surviving OSD also fails before then, they are lost and Ceph may need manual help to
choose between the two histories. With 2, no write is ever acknowledged on a single copy,
but the same crash stops all I/O on both pools until the OSD returns, which is the
single-copy failure we wanted to avoid. For a lab whose runs are checksummed and can be
repeated, availability is worth more, hence 1. While node I is down its pool is served by
node I+1, which the cluster file and the scheduler do not know.

### Extra network traffic the scheduler does not yet charge

Every write to pool `node<I>` is sent by node I's OSD to node I+1's OSD before it is
acknowledged. The OSDs use their node addresses, so that copy crosses the shaped network:
it leaves through node I's link and router port and arrives through node I+1's. So:

- every output write moves its bytes over the network once more, even when the task ran on
  node I and wrote locally, and the write stage lasts until the copy has arrived;
- it uses node I's transmit capacity, the same capacity node I needs to serve reads to
  other nodes, and node I+1's receive capacity;
- loading a dataset moves it once over the network too, and recovery after an OSD restart
  moves the changed objects again.

The forecast model (`Simulation.stages_for` in `cephdag/model.py`) charges a write to
`write:<target>`, plus `tx:<worker>` and `rx:<target>` when the task ran elsewhere. With
two replicas each write also needs `tx:<target>`, `rx:<target+1>` and `write:<target+1>`,
and `transfer_bytes` misses the copy. Until the model charges them it underestimates write
time and network bytes, so policy results from a two-replica lab are not comparable with
one-copy results. Reads are unaffected: they come from the primary only.

## Host budget

`plan` prints what a lab takes from the host and `up` refuses a lab that does not fit:

- in each mode, the OSD shares, the shares of the workers that run and the router (0.25)
  must not exceed the host's logical CPUs. Container quotas can add up to more in disagg mode,
  where the storage nodes keep their whole quota with their worker share idle;
- the memory limits of all containers plus the router (128 MiB) must leave 8 GiB for the host;
- the logical OSD sizes together must be at most 460 GiB, the part of the SSD a lab may take
  (the existing lab is not counted), and must leave 80 GiB free on the disk holding Docker's
  data root. BlueStore block files are sparse, so this bounds what the lab can ever use.

OSDs stay on Docker volumes on the SSD. On one shared hard disk each of eight OSDs would get
about 30 MB/s, less than a 400 Mbit/s link carries, so remote reads would be faster than
local ones and the comparison would be inverted. Results go to `results_dir` on the hard disk.

The data a lab holds is less than its OSD sizes: Ceph warns at 85% of an OSD (nearfull)
and stops writes at 95%. `plan` reports the data, inputs and outputs together, that fits
below 85% when it is spread evenly over the storage nodes. Each pool lives on one OSD, so
data that is not spread evenly fills its node's OSD first.

For aakashpc (32 logical CPUs, 123 GiB, about 1126 GiB free; rerun `plan` there):

| Config | CPU, cs | CPU, disagg | Quotas, disagg | Memory | OSDs | Data below nearfull |
| --- | --- | --- | --- | --- | --- | --- |
| `cephdag8`, 1 copy | 24.25 of 32 | 24.25 of 32 | 40.25 | 72.1 GiB | 448 GiB | 381 GiB, 48 GiB per node |

## The image

The Dockerfile installs the wheels named in `requirements-linux-amd64.lock` and
`requirements-droid-linux-amd64.lock` from `deps/wheels/` (not in git) in one pip run with
`--require-hashes` and `--no-index`. Copy numpy and pip from the refinement project on
aakashpc and check their SHA-256 against the lock. The DROID lock pins one cp39 manylinux
x86_64 wheel per package (av, pillow, pyarrow, from the droid branch's
`requirements-droid.txt`); fetch them with any pip:

```sh
python3 -m pip download --no-deps --only-binary :all: --platform manylinux_2_28_x86_64 \
    --python-version 3.9 --implementation cp --abi cp39 \
    -r infra/requirements-droid-linux-amd64.lock -d deps/wheels
```

Packages pinned in the main lock, such as numpy, must not be pinned again. The image holds
`cephdag/`, `experiments/` and the cluster files as they were at `build`; rebuild after
changing them.

The Rust planner (beam, MCTS) is built in a first stage from the same Ceph image, so it
links against the image's glibc, with the official Rust 1.85.0 `rustc`, `rust-std` and
`cargo` archives for x86_64 Linux from static.rust-lang.org placed in `deps/rust/` (not in
git); `infra/rust-toolchain.sha256` pins them and the build checks it. The library lands
at `/app/native/target/release/libcephdag_native.so`, where `cephdag/policies/native.py`
loads it.

## What only a real run can show

The tests run the whole lifecycle against a recording fake of Docker and Ceph
(`tests/fake_host.py`). They prove which commands are issued, not that Ceph accepts them.
Untested until the first deployment: a router with 16 networks and a Compose project with 16
internal networks; the bind mount of `results_dir` (files written there are owned by root);
`ceph.conf` and the keyring in compute-only containers without a data volume; stopping and
starting compute-only containers, the readiness test of their entrypoint and the reshaping
after it; `pkill` of a worker and the `POST /state` readiness probe; the decompile-append-
compile path of the two-replica CRUSH rules; the JSON shape of `ceph pg ls-by-pool` in Ceph 20
(`verify` reads `pg_stats`); the time and router load of the ring bandwidth check; a build
with the DROID lock in the Ceph image; and whether 8 OSDs keep within `osd_memory_target`
under load.
