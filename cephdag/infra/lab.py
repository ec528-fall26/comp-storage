#!/usr/bin/env python3
"""Lifecycle of one lab described by infra/labs/<name>.json. Never reuses an existing cluster."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Optional
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
GIB = 2**30
MIB = 2**20
PG_COUNT = 32
WORKER_PORT = 8080
WORKER = "python3 -m cephdag.worker"
MODES = ("cs", "disagg", "disagg2", "cs2")
COMPUTATIONAL_STORAGE = ("cs", "cs2")
PIDS_LIMIT = 512
ROUTER_CPUS = 0.25
ROUTER_MEMORY_MIB = 128
HOST_MEMORY_RESERVE = 8 * GIB
# A worker's 512 MiB task reservation (cephdag.graph.WORKER_MEMORY), its own memory (90-300
# MiB RSS measured on cephdag8) and a margin.
WORKER_PROCESS_MIB = 1024
HOST_DISK_RESERVE = 80 * GIB
OSD_DISK_CAP = 460 * GIB
NEARFULL_RATIO = 0.85
TBF_LIMITS = ["burst", "128kb", "latency", "400ms"]
MON_KEYRING = "/var/lib/ceph/mon.keyring"
ADMIN_KEYRING = "/var/lib/ceph/admin.keyring"
SMOKE = """import hashlib, json, sys
import numpy as np
import rados
c = rados.Rados(conffile='/etc/ceph/ceph.conf')
c.connect()
a = np.random.default_rng(9917).random(262144, dtype=np.float32).tobytes()
rows = []
for n in range(int(sys.argv[1])):
    with c.open_ioctx('node' + str(n)) as io:
        io.write_full('smoke/a', a)
        b = io.read('smoke/a', len(a))
        assert b == a
        rows.append({'node': n, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()})
c.shutdown()
print(json.dumps(rows))
"""
WORKERS_READY = """import json, sys, urllib.request
for url in sys.argv[1:]:
    request = urllib.request.Request(url + '/state', data=b'{"keys": []}', method='POST')
    with urllib.request.urlopen(request, timeout=5) as response:
        assert 'retention_protocol' in json.load(response), url
"""


@dataclass(frozen=True)
class Lab:
    """Storage nodes, each with one OSD, its pool and a worker, then compute-only nodes with a
    worker and no data, joined by one router. Node I is container n<I> if it stores data, else
    c<I - storage_nodes>.

    Mode cs runs the storage nodes' workers and stops the compute-only containers; mode disagg
    runs only the compute-only nodes' workers. Modes cs2 and disagg2 run two workers in each
    container of cs and disagg, on WORKER_PORT and the next port, sharing its worker CPUs,
    memory and link; their cluster files list each container's second worker as one more node
    without a pool, which runs on the host of the first (Cluster.hosts). Every mode uses the
    same Ceph cluster and data.
    `results_dir` is mounted at /results in every node; null mounts nothing.
    """

    name: str
    image: str
    storage_nodes: int
    compute_nodes: int
    osd_cpus: float
    worker_cpus: float
    osd_memory_mib: int
    worker_memory_mib: int
    osd_gib: int
    worker_slots: int
    first_subnet: str
    bandwidth_mbit: float
    replicas: int
    results_dir: Optional[str] = "/mnt/mass/cephdag"

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9]*", self.name):
            raise ValueError(
                "a lab name is lowercase letters and digits; it prefixes every resource"
            )
        if self.storage_nodes < 3:
            raise ValueError(
                "a lab has at least 3 storage nodes: verify loads each with two others"
            )
        if not 0 <= self.compute_nodes <= 256 - self.storage_nodes:
            raise ValueError("a lab has at most 256 nodes: node I tunnels through 10.77.I.0/30")
        if self.worker_slots != 1:
            raise ValueError("workers run one task at a time; worker_slots must be 1")
        if self.replicas not in (1, 2):
            raise ValueError("replicas must be 1 or 2")
        shares = (self.osd_cpus, self.worker_cpus, self.osd_memory_mib, self.worker_memory_mib)
        if min(*shares, self.osd_gib, self.bandwidth_mbit) <= 0:
            raise ValueError("CPU and memory shares, OSD size and bandwidth must be positive")
        if ipaddress.ip_network(self.first_subnet).prefixlen != 24:
            raise ValueError("first_subnet must be a /24")
        if not self.subnet(self.nodes - 1).is_private:
            raise ValueError("every node subnet must be private")
        if self.results_dir is not None and not Path(self.results_dir).is_absolute():
            raise ValueError("results_dir must be an absolute path")

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text()))

    @property
    def nodes(self):
        return self.storage_nodes + self.compute_nodes

    @property
    def storage(self):
        return range(self.storage_nodes)

    @property
    def compute(self):
        return range(self.storage_nodes, self.nodes)

    @property
    def modes(self):
        return MODES if self.compute_nodes else MODES[:1]

    def active(self, mode):
        """The nodes whose containers run in `mode`."""
        return self.storage if mode in COMPUTATIONAL_STORAGE else range(self.nodes)

    def workers(self, mode):
        """The nodes whose workers run in `mode`."""
        return self.storage if mode in COMPUTATIONAL_STORAGE else self.compute

    def slots(self, mode):
        """Worker processes in each node of `workers(mode)`."""
        return 2 if mode in ("cs2", "disagg2") else 1

    def cpus(self, node):
        return self.osd_cpus + self.worker_cpus if node in self.storage else self.worker_cpus

    def memory_mib(self, node):
        if node in self.storage:
            return self.osd_memory_mib + self.worker_memory_mib
        return self.worker_memory_mib

    @property
    def osd_memory_target(self):
        """80% of the OSD's memory share, leaving headroom for the OSD to overshoot its target."""
        return self.osd_memory_mib * MIB * 4 // 5

    def cluster_path(self, mode):
        return f"cephdag/clusters/{self.name}-{mode}.json"

    @property
    def state(self):
        return ROOT / "state" / self.name

    @property
    def results(self):
        return ROOT / "results" / "deployment" / self.name

    def subnet(self, node):
        first = ipaddress.ip_network(self.first_subnet)
        return ipaddress.ip_network((first.network_address + node * first.num_addresses, 24))

    def address(self, node):
        return str(self.subnet(node).network_address + 10)

    def router_address(self, node):
        return str(self.subnet(node).network_address + 254)

    def service(self, node):
        if node == "router":
            return node
        return f"n{node}" if node in self.storage else f"c{node - self.storage_nodes}"

    def container(self, node):
        return f"{self.name}-{self.service(node)}"

    @property
    def containers(self):
        return [self.container(node) for node in range(self.nodes)] + [self.container("router")]

    @property
    def volumes(self):
        return [f"{self.name}_osd{node}" for node in self.storage]

    @property
    def networks(self):
        return [f"{self.name}_link{node}" for node in range(self.nodes)]

    def replica_osds(self, node):
        """The OSDs that hold pool `node<node>`, primary first: its own, then the next node's."""
        return [node, (node + 1) % self.storage_nodes][: self.replicas]


def compose(lab):
    """The Docker Compose project that `up` creates, before ownership labels are added."""
    subnets = ",".join(str(lab.subnet(node)) for node in range(lab.nodes))
    bandwidth = f"{lab.bandwidth_mbit:g}"
    services = {}
    for node in range(lab.nodes):
        services[lab.service(node)] = {
            "image": lab.image,
            "container_name": lab.container(node),
            "hostname": lab.service(node),
            "cpus": lab.cpus(node),
            "mem_limit": f"{lab.memory_mib(node)}m",
            "memswap_limit": f"{lab.memory_mib(node)}m",
            "pids_limit": PIDS_LIMIT,
            "cap_add": ["NET_ADMIN"],
            "environment": {
                "ROLE": "node",
                "NODE": str(node),
                "BANDWIDTH_MBIT": bandwidth,
                "SUBNETS": subnets,
            },
            "networks": {f"link{node}": {"ipv4_address": lab.address(node)}},
            "restart": "no",
        }
        if node_volumes(lab, node):
            services[lab.service(node)]["volumes"] = node_volumes(lab, node)
    services["router"] = {
        "image": lab.image,
        "container_name": lab.container("router"),
        "cpus": ROUTER_CPUS,
        "mem_limit": f"{ROUTER_MEMORY_MIB}m",
        "memswap_limit": f"{ROUTER_MEMORY_MIB}m",
        "cap_add": ["NET_ADMIN"],
        "sysctls": {"net.ipv4.ip_forward": "1"},
        "environment": {"ROLE": "router", "BANDWIDTH_MBIT": bandwidth, "SUBNETS": subnets},
        "networks": {
            f"link{node}": {"ipv4_address": lab.router_address(node)} for node in range(lab.nodes)
        },
        "restart": "no",
    }
    networks = {
        f"link{node}": {
            "internal": True,
            "enable_ipv6": False,
            "ipam": {"config": [{"subnet": str(lab.subnet(node))}]},
        }
        for node in range(lab.nodes)
    }
    volumes = {f"osd{node}": {} for node in lab.storage}
    return {"name": lab.name, "services": services, "networks": networks, "volumes": volumes}


def data_volume(node):
    return {
        "type": "volume",
        "source": f"osd{node}",
        "target": "/var/lib/ceph",
        "volume": {"nocopy": True},
    }


def node_volumes(lab, node):
    """A storage node's private Ceph data volume, and the results directory on every node."""
    volumes = [data_volume(node)] if node in lab.storage else []
    if lab.results_dir is not None:
        volumes.append({"type": "bind", "source": lab.results_dir, "target": "/results"})
    return volumes


def validate_compose(lab, config):
    nodes = {lab.service(node): node for node in range(lab.nodes)}
    if config["name"] != lab.name or set(config["services"]) != set(nodes) | {"router"}:
        raise ValueError("unexpected project or services")
    for name, service in config["services"].items():
        for forbidden in ("privileged", "ports", "network_mode", "devices", "pid", "ipc"):
            if service.get(forbidden):
                raise ValueError(f"forbidden host exposure: {forbidden}")
        if set(service.get("cap_add", [])) != {"NET_ADMIN"}:
            raise ValueError("only NET_ADMIN is allowed")
        if name == "router":
            links = {f"link{node}" for node in range(lab.nodes)}
            volumes = []
        else:
            links = {f"link{nodes[name]}"}
            volumes = node_volumes(lab, nodes[name])
        if set(service["networks"]) != links:
            raise ValueError("node must have exactly its own NIC boundary")
        if service.get("volumes", []) != volumes:
            raise ValueError("nodes mount only their private Ceph data and the results directory")
    if not all(network.get("internal") for network in config["networks"].values()):
        raise ValueError("all networks must be internal")


def ceph_conf(lab, fsid, node):
    monitor = f"[v2:{lab.address(0)}:3300,v1:{lab.address(0)}:6789]"
    conf = f"""[global]
fsid = {fsid}
mon_initial_members = a
mon_host = {monitor}
auth_cluster_required = cephx
auth_service_required = cephx
auth_client_required = cephx
osd_pool_default_size = {lab.replicas}
osd_pool_default_min_size = 1
mon_allow_pool_size_one = true
osd_pool_default_pg_autoscale_mode = off
log_to_file = false
log_to_stderr = true
err_to_stderr = true
mon_cluster_log_to_file = false
osd_memory_target = {lab.osd_memory_target}
osd_memory_target_autotune = false
bluestore_block_size = {lab.osd_gib * GIB}
bluestore_compression_mode = none
osd_scrub_begin_hour = 0
osd_scrub_end_hour = 0
[client]
keyring = /var/lib/ceph/admin.keyring
"""
    if node in lab.storage:
        conf += f"""
[osd]
public_addr = {lab.address(node)}
cluster_addr = {lab.address(node)}
"""
    return conf


def crush_rules(lab):
    """One rule per pool: node I's OSD first, so it is the primary, then the replica's OSD."""
    rules = []
    for node in lab.storage:
        steps = "".join(
            f"\tstep take n{osd}\n\tstep choose firstn 1 type osd\n\tstep emit\n"
            for osd in lab.replica_osds(node)
        )
        rules.append(f"rule pin{node} {{\n\tid {node + 1}\n\ttype replicated\n{steps}}}\n")
    return "".join(rules)


def pool_commands(lab):
    """Ceph commands creating pool `node<I>` for every node I, pinned by CRUSH rule `pin<I>`."""
    pgs = str(PG_COUNT)
    commands = []
    for node in lab.storage:
        pool = f"node{node}"
        rule = f"pin{node}"
        if lab.replicas == 1:
            commands.append(["osd", "crush", "rule", "create-replicated", rule, f"n{node}", "osd"])
        commands += [
            ["osd", "pool", "create", pool, pgs, pgs, "replicated", rule],
            ["osd", "pool", "set", pool, "size", str(lab.replicas), "--yes-i-really-mean-it"],
            ["osd", "pool", "set", pool, "min_size", "1"],
            ["osd", "pool", "application", "enable", pool, "cephdag"],
        ]
    return commands


def tbf(dev, mbit):
    return ["tc", "qdisc", "replace", "dev", dev, "root", "tbf", "rate", f"{mbit}mbit", *TBF_LIMITS]


def ring_flows(ring, node):
    """iperf3 flows (source, destination, port) that load `node`'s boundary with its two successors in `ring`."""
    index = ring.index(node)
    first = ring[(index + 1) % len(ring)]
    second = ring[(index + 2) % len(ring)]
    return {
        "shared_tx": [(node, first, 5201), (node, second, 5201)],
        "shared_rx": [(first, node, 5201), (second, node, 5202)],
    }


def worker_url(lab, node, slot=0):
    return f"http://{lab.address(node)}:{WORKER_PORT + slot}"


def cluster_file(lab, mode):
    """The cluster file (cephdag/cluster.py) of `mode`: every node that runs in it, with its pool
    if it stores data and its worker if that runs in `mode`, then a compute-only node for every
    further worker slot of those nodes."""
    nodes = [
        {
            "pool": f"node{node}" if node in lab.storage else None,
            "worker": worker_url(lab, node) if node in lab.workers(mode) else None,
        }
        for node in lab.active(mode)
    ]
    nodes += [
        {"pool": None, "worker": worker_url(lab, node, slot)}
        for slot in range(1, lab.slots(mode))
        for node in lab.workers(mode)
    ]
    return {
        "nodes": nodes,
        "metadata_pool": "node0",
        "ceph_conf": "/etc/ceph/ceph.conf",
        "namespace": "cephdag",
    }


def host_resources(disk):
    """Logical CPUs, memory bytes and free bytes on the filesystem holding `disk`."""
    meminfo = Path("/proc/meminfo").read_text()
    memory = int(re.search(r"^MemTotal:\s+(\d+) kB", meminfo, re.MULTILINE).group(1)) * 1024
    return os.cpu_count(), memory, shutil.disk_usage(disk).free


def budget(lab, cpus, memory, free_disk):
    """What the lab takes from the host, and the reasons it does not fit (empty if it does).

    CPUs count, per mode, the OSD shares and the shares of the workers that run: in disagg mode a
    storage node keeps its whole quota, but its worker share is idle. The worker processes of
    a container share its worker memory, WORKER_PROCESS_MIB each.
    """
    use = {
        "cpus": {
            mode: lab.storage_nodes * lab.osd_cpus
            + len(lab.workers(mode)) * lab.worker_cpus
            + ROUTER_CPUS
            for mode in lab.modes
        },
        "memory": (sum(map(lab.memory_mib, range(lab.nodes))) + ROUTER_MEMORY_MIB) * MIB,
        "osd_disk": lab.storage_nodes * lab.osd_gib * GIB,
    }
    problems = []
    for mode, shares in use["cpus"].items():
        if shares > cpus:
            problems.append(f"{mode} mode uses {shares:g} CPUs, more than the host's {cpus:g}")
    for mode in lab.modes:
        if lab.worker_memory_mib < lab.slots(mode) * WORKER_PROCESS_MIB:
            problems.append(
                f"{mode} mode runs {lab.slots(mode)} workers in {lab.worker_memory_mib} MiB of "
                f"worker memory, less than {WORKER_PROCESS_MIB} MiB each"
            )
    if use["memory"] > memory - HOST_MEMORY_RESERVE:
        problems.append(
            f"memory limits {use['memory'] / GIB:.1f} GiB exceed host memory {memory / GIB:.1f} GiB "
            f"less a {HOST_MEMORY_RESERVE // GIB} GiB reserve"
        )
    if use["osd_disk"] > OSD_DISK_CAP:
        problems.append(
            f"OSD sizes {use['osd_disk'] / GIB:.0f} GiB exceed the {OSD_DISK_CAP // GIB} GiB "
            "of SSD one lab may take"
        )
    if use["osd_disk"] > free_disk - HOST_DISK_RESERVE:
        problems.append(
            f"OSD sizes {use['osd_disk'] / GIB:.0f} GiB exceed free disk {free_disk / GIB:.0f} GiB "
            f"less a {HOST_DISK_RESERVE // GIB} GiB reserve"
        )
    return use, problems


def copies(lab):
    return "1 copy" if lab.replicas == 1 else f"{lab.replicas} copies"


def budget_report(lab, cpus, memory, free_disk):
    use, problems = budget(lab, cpus, memory, free_disk)
    lines = []
    for mode, shares in use["cpus"].items():
        quotas = sum(map(lab.cpus, lab.active(mode))) + ROUTER_CPUS
        workers = f"{len(lab.workers(mode))} workers"
        if lab.slots(mode) > 1:
            workers = f"{len(lab.workers(mode))} nodes of {lab.slots(mode)} workers"
        lines.append(
            f"CPU {mode:<9}{shares:g} of {cpus:g} CPUs ({lab.storage_nodes} OSDs x "
            f"{lab.osd_cpus:g} + {workers} x {lab.worker_cpus:g} + router "
            f"{ROUTER_CPUS:g}); container quotas {quotas:g}"
        )
    per_pool = lab.osd_gib * NEARFULL_RATIO / lab.replicas
    memory_limits = [f"{lab.storage_nodes} x {lab.memory_mib(0)} MiB"]
    if lab.compute_nodes:
        memory_limits.append(f"{lab.compute_nodes} x {lab.worker_memory_mib} MiB")
    lines += [
        f"memory       {use['memory'] / GIB:.1f} of {memory / GIB:.1f} GiB with every container "
        f"running, {HOST_MEMORY_RESERVE // GIB} GiB kept for the host "
        f"({' + '.join(memory_limits)} + router {ROUTER_MEMORY_MIB} MiB); "
        f"osd_memory_target {lab.osd_memory_target / GIB:.2f} GiB",
        f"workers      {lab.worker_memory_mib} MiB of worker memory per container, at least "
        f"{WORKER_PROCESS_MIB} MiB per worker: "
        + ", ".join(f"{mode} {lab.worker_memory_mib // lab.slots(mode)} MiB" for mode in lab.modes),
        f"OSD disk     {use['osd_disk'] / GIB:.0f} GiB logical, at most {OSD_DISK_CAP // GIB} GiB; "
        f"{free_disk / GIB:.0f} GiB free on Docker's disk, {HOST_DISK_RESERVE // GIB} GiB kept "
        f"free ({lab.storage_nodes} x {lab.osd_gib} GiB)",
        f"data         {per_pool * lab.storage_nodes:.0f} GiB of inputs and outputs, "
        f"{per_pool:.0f} GiB per node pool, stored as {copies(lab)}, before Ceph's nearfull "
        f"mark at {NEARFULL_RATIO:.0%} of each OSD, if spread evenly over the storage nodes",
        f"results      {lab.results_dir}, mounted at /results in every node; up and bootstrap "
        "require it to be a writable directory"
        if lab.results_dir
        else "results      none mounted",
    ]
    return lines + [f"REFUSED: {problem}" for problem in problems], problems


def require_budget(lab, cpus, memory, free_disk):
    _, problems = budget(lab, cpus, memory, free_disk)
    if problems:
        raise RuntimeError("lab does not fit this host: " + "; ".join(problems))


def require_results_dir(lab):
    if lab.results_dir is None:
        return
    path = Path(lab.results_dir)
    if not path.is_dir():
        raise RuntimeError(
            f"results directory {path} does not exist; mount the archive disk and create it"
        )
    if not os.access(path, os.W_OK | os.X_OK):
        raise RuntimeError(f"results directory {path} is not writable by this user")


def require_fresh(lab, containers, volumes, networks):
    if (
        set(containers) & set(lab.containers)
        or set(volumes) & set(lab.volumes)
        or set(networks) & set(lab.networks)
    ):
        raise RuntimeError("refusing to reuse existing lab-named resources; inspect manually")


def require_free_subnets(lab, networks):
    """`networks` is `docker network inspect` output for every existing Docker network."""
    proposed = [lab.subnet(node) for node in range(lab.nodes)]
    for network in networks:
        for item in network.get("IPAM", {}).get("Config") or []:
            subnet = item.get("Subnet", "")
            if (
                subnet
                and ":" not in subnet
                and any(ipaddress.ip_network(subnet).overlaps(p) for p in proposed)
            ):
                raise RuntimeError(
                    f"proposed subnet overlaps existing Docker network {network.get('Name')}"
                )


def validate_placement(osds, mapping):
    """`mapping` is one placement group from `ceph osd map` or `ceph pg ls-by-pool`."""
    if (
        mapping.get("up") != osds
        or mapping.get("acting") != osds
        or mapping.get("up_primary") != osds[0]
        or mapping.get("acting_primary") != osds[0]
    ):
        raise RuntimeError(f"placement is not {osds} with primary osd.{osds[0]}: {mapping}")


def pgs_clean(status):
    summary = status.get("pg_summary", status)
    rows = summary.get("num_pg_by_state", [])
    return bool(rows) and all(x.get("name", x.get("state_name")) == "active+clean" for x in rows)


def require_bandwidth(rates, mbit):
    """`rates` maps node to its shared TX and RX Mbit/s; each must be 70-120% of `mbit`."""
    failed = sorted(
        node
        for node, values in rates.items()
        if not all(0.7 * mbit <= v <= 1.2 * mbit for v in values.values())
    )
    if failed:
        raise RuntimeError(f"shaping validation failed at nodes {failed}: {rates}")


def command(args, *, data=None, timeout=120):
    result = subprocess.run(
        args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, cwd=ROOT
    )
    if result.returncode:
        # Never print stdin (may be a fresh lab keyring).
        raise RuntimeError(
            f"{shlex.join(args)} exited {result.returncode}: "
            + result.stderr.decode(errors="replace")[-3000:]
        )
    return result.stdout.decode()


def docker(*args, **kwargs):
    return command(["docker", *args], **kwargs)


def inside(lab, node, *args, data=None, detached=False, timeout=120):
    flags = ["-i"] if data is not None else []
    if detached:
        flags.append("-d")
    return docker("exec", *flags, lab.container(node), *args, data=data, timeout=timeout)


def write_inside(lab, node, path, content):
    inside(lab, node, "bash", "-c", "umask 077; cat > " + shlex.quote(path), data=content.encode())


def ceph(lab, *args):
    return inside(lab, 0, "ceph", *args)


def approved(lab):
    evidence = lab.state / "approval.json"
    if not evidence.exists() or not json.loads(evidence.read_text()).get("approved"):
        raise RuntimeError(
            f"deployment permission pending; {evidence} must record explicit approval"
        )


def save(lab, name, value):
    lab.results.mkdir(parents=True, exist_ok=True)
    (lab.results / name).write_text(
        value if isinstance(value, str) else json.dumps(value, indent=2) + "\n"
    )


def identity(lab):
    return json.loads((lab.state / "cluster.json").read_text())


def assert_owned(lab):
    """Check that every container is the lab's; return the names of those running."""
    expected = identity(lab)
    running = set()
    for name in lab.containers:
        info = json.loads(docker("inspect", name))[0]
        if info["Config"]["Labels"].get("org.cephdag.lab") != expected["fsid"]:
            raise RuntimeError(f"container ownership mismatch: {name}")
        if info["State"]["Running"]:
            running.add(name)
    return running


def build(lab):
    approved(lab)
    validate_compose(lab, compose(lab))
    print(docker("build", "-f", "infra/Dockerfile", "-t", lab.image, ".", timeout=900))


def up(lab):
    approved(lab)
    config = compose(lab)
    validate_compose(lab, config)
    if (lab.state / "cluster.json").exists():
        raise RuntimeError("cluster identity already exists; do not create/reinitialize again")
    require_results_dir(lab)
    containers = docker("ps", "-a", "--format", "{{.Names}}").splitlines()
    volumes = docker("volume", "ls", "--format", "{{.Name}}").splitlines()
    networks = json.loads(docker("network", "inspect", *docker("network", "ls", "-q").split()))
    require_fresh(lab, containers, volumes, [network["Name"] for network in networks])
    require_free_subnets(lab, networks)
    require_budget(lab, *host_resources(docker("info", "--format", "{{.DockerRootDir}}").strip()))
    fsid = str(uuid.uuid4())
    for service in config["services"].values():
        service["labels"] = {"org.cephdag.lab": fsid}
    for volume in config["volumes"].values():
        volume["labels"] = {"org.cephdag.lab": fsid}
    lab.state.mkdir(parents=True, exist_ok=True)
    (lab.state / "compose.json").write_text(json.dumps(config, indent=2))
    (lab.state / "cluster.json").write_text(json.dumps({"fsid": fsid, "created": time.time()}))
    print(docker("compose", "-f", str(lab.state / "compose.json"), "up", "-d", "--no-build"))
    assert_owned(lab)


def bootstrap(lab):
    approved(lab)
    require_results_dir(lab)
    assert_owned(lab)
    if (lab.state / "bootstrapped").exists():
        raise RuntimeError("already bootstrapped; never reformat")
    # Refuse even an interrupted prior bootstrap unless a human inspects it.
    for node in lab.storage:
        if inside(lab, node, "find", "/var/lib/ceph", "-mindepth", "1", "-maxdepth", "1").strip():
            raise RuntimeError(f"node {node} Ceph volume is nonempty; refusing initialization")
    fsid = identity(lab)["fsid"]
    for node in range(lab.nodes):
        write_inside(lab, node, "/etc/ceph/ceph.conf", ceph_conf(lab, fsid, node))
    inside(lab, 0, "ceph-authtool", "--create-keyring", MON_KEYRING, "--gen-key", "-n", "mon.", "--cap", "mon", "allow *")  # fmt: skip
    inside(
        lab, 0, "ceph-authtool", "--create-keyring", ADMIN_KEYRING, "--gen-key", "-n", "client.admin",
        "--cap", "mon", "allow *", "--cap", "osd", "allow *", "--cap", "mgr", "allow *", "--cap", "mds", "allow *",
    )  # fmt: skip
    inside(lab, 0, "ceph-authtool", MON_KEYRING, "--import-keyring", ADMIN_KEYRING)
    key = inside(lab, 0, "cat", ADMIN_KEYRING)
    for node in range(1, lab.nodes):
        write_inside(lab, node, ADMIN_KEYRING, key)
    del key
    monitor = f"[v2:{lab.address(0)}:3300,v1:{lab.address(0)}:6789]"
    inside(lab, 0, "monmaptool", "--create", "--addv", "a", monitor, "--fsid", fsid, "/tmp/monmap")
    inside(lab, 0, "mkdir", "-p", "/var/lib/ceph/mon/ceph-a")
    inside(
        lab, 0, "ceph-mon", "--mkfs", "-i", "a", "--monmap", "/tmp/monmap", "--keyring", MON_KEYRING,
        "--setuser", "root", "--setgroup", "root",
    )  # fmt: skip
    daemon(
        lab, 0, "exec ceph-mon -f -i a --setuser root --setgroup root > /var/lib/ceph/mon.log 2>&1"
    )
    wait_for(lambda: json.loads(ceph(lab, "status", "-f", "json")).get("fsid") == fsid)
    inside(lab, 0, "mkdir", "-p", "/var/lib/ceph/mgr/ceph-a")
    ceph(
        lab, "auth", "get-or-create", "mgr.a", "mon", "allow profile mgr", "osd", "allow *", "mds", "allow *",
        "-o", "/var/lib/ceph/mgr/ceph-a/keyring",
    )  # fmt: skip
    daemon(
        lab, 0, "exec ceph-mgr -f -i a --setuser root --setgroup root > /var/lib/ceph/mgr.log 2>&1"
    )
    for node in lab.storage:
        create_osd(lab, node)
    wait_for(lambda: osds_up(lab) == lab.storage_nodes)
    if lab.replicas > 1:
        install_crush_rules(lab, crush_rules(lab))
    for args in pool_commands(lab):
        ceph(lab, *args)
    ceph(lab, "osd", "set", "noscrub")
    ceph(lab, "osd", "set", "nodeep-scrub")
    wait_for(lambda: pgs_clean(json.loads(ceph(lab, "pg", "stat", "-f", "json"))))
    (lab.state / "bootstrapped").write_text(fsid)
    print("Fresh Ceph bootstrap complete; validation still required.")


def daemon(lab, node, line):
    inside(lab, node, "bash", "-c", line, detached=True)


def create_osd(lab, node):
    osd_uuid = str(uuid.uuid4())
    got = ceph(lab, "osd", "create", osd_uuid, str(node)).strip()
    if got != str(node):
        raise RuntimeError(f"unexpected OSD ID: {got}")
    path = f"/var/lib/ceph/osd/ceph-{node}"
    inside(lab, node, "mkdir", "-p", path)
    keyring = path + "/keyring"
    inside(
        lab, node, "ceph-authtool", "--create-keyring", keyring, "--gen-key", "-n", f"osd.{node}"
    )
    osd_key = inside(lab, node, "cat", keyring)
    inside(
        lab, 0, "ceph", "auth", "add", f"osd.{node}", "mon", "allow profile osd", "osd", "allow *", "-i", "-",
        data=osd_key.encode(),
    )  # fmt: skip
    del osd_key
    inside(
        lab, node, "ceph-osd", "--mkfs", "-i", str(node), "--osd-uuid", osd_uuid, "--setuser", "root",
        "--setgroup", "root", timeout=180,
    )  # fmt: skip
    weight = str(lab.osd_gib / 1024)
    ceph(lab, "osd", "crush", "add", f"osd.{node}", weight, "root=default", f"host=n{node}")
    daemon(
        lab,
        node,
        f"exec ceph-osd -f -i {node} --setuser root --setgroup root > /var/lib/ceph/osd.log 2>&1",
    )


def osds_up(lab):
    osds = json.loads(ceph(lab, "osd", "dump", "-f", "json"))["osds"]
    return len([x for x in osds if x["up"] and x["in"]])


def install_crush_rules(lab, rules):
    """`ceph osd crush rule create-replicated` takes one bucket, so multi-bucket rules go in as text."""
    ceph(lab, "osd", "getcrushmap", "-o", "/tmp/crush.bin")
    current = inside(lab, 0, "crushtool", "-d", "/tmp/crush.bin")
    write_inside(lab, 0, "/tmp/crush.txt", current + rules)
    inside(lab, 0, "crushtool", "-c", "/tmp/crush.txt", "-o", "/tmp/crush.new")
    ceph(lab, "osd", "setcrushmap", "-i", "/tmp/crush.new")


def wait_for(predicate, what="Ceph readiness", seconds=120):
    until = time.monotonic() + seconds
    last = None
    while time.monotonic() < until:
        try:
            if predicate():
                return
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last = str(exc)
        time.sleep(1)
    raise RuntimeError(f"{what} timeout: {last}")


def resume(lab):
    approved(lab)
    assert_owned(lab)
    if (lab.state / "bootstrapped").read_text().strip() != identity(lab)["fsid"]:
        raise RuntimeError("bootstrap identity mismatch; refusing resume")
    docker("start", *lab.containers)
    for node in range(lab.nodes):
        if identity(lab)["fsid"] not in inside(lab, node, "cat", "/etc/ceph/ceph.conf"):
            raise RuntimeError("container config belongs to another cluster")
    daemons = [(0, "ceph-mon", ["-i", "a"], "mon"), (0, "ceph-mgr", ["-i", "a"], "mgr")]
    daemons += [(node, "ceph-osd", ["-i", str(node)], "osd") for node in lab.storage]
    for node, process, args, log in daemons:
        if process not in inside(lab, node, "ps", "-eo", "comm").splitlines():
            line = shlex.join([process, "-f", *args, "--setuser", "root", "--setgroup", "root"])
            daemon(lab, node, f"exec {line} >> /var/lib/ceph/{log}.log 2>&1")
    wait_for(lambda: pgs_clean(json.loads(ceph(lab, "pg", "stat", "-f", "json"))))
    if (lab.state / "shaped.json").exists():
        shape(lab, shaped_mbit(lab))
    print(ceph(lab, "status"))


def switch_mode(lab, mode, functions):
    """Run the workers of `mode` and no others, starting or stopping the compute-only containers."""
    approved(lab)
    if mode not in lab.modes:
        raise ValueError(f"lab {lab.name} has no compute-only nodes; its only mode is cs")
    running = assert_owned(lab)
    if not {lab.container(node) for node in [*lab.storage, "router"]} <= running:
        raise RuntimeError("storage nodes or router are stopped; resume the lab first")
    if mode in COMPUTATIONAL_STORAGE:
        stop_compute(lab, running)
    else:
        for node in lab.storage:
            stop_worker(lab, node)
        start_compute(lab, running)
    for node in lab.workers(mode):
        start_workers(lab, node, mode, functions)
    urls = [node["worker"] for node in cluster_file(lab, mode)["nodes"] if node["worker"]]
    wait_for(lambda: inside(lab, 0, "python3", "-c", WORKERS_READY, *urls) == "", "workers")
    print(f"{lab.name} is in mode {mode}: {len(urls)} workers answer /state")


def stop_workers(lab):
    """Stop the storage nodes' workers and the compute-only containers; Ceph keeps running."""
    approved(lab)
    running = assert_owned(lab)
    for node in lab.storage:
        if lab.container(node) in running:
            stop_worker(lab, node)
    stop_compute(lab, running)


def running_workers(lab, node):
    """The command line, as a list of words, of every worker in `node`'s container."""
    processes = inside(lab, node, "ps", "-eo", "args").splitlines()
    return [line.split() for line in processes if line.startswith(WORKER)]


def worker_running(lab, node):
    return bool(running_workers(lab, node))


def worker_nodes(lab, mode):
    """(cluster node, lab node) of every worker of `mode`: the worker serves that entry of the
    cluster file and runs in the container whose address its URL has."""
    containers = {lab.address(node): node for node in range(lab.nodes)}
    return [
        (index, containers[urlsplit(entry["worker"]).hostname])
        for index, entry in enumerate(cluster_file(lab, mode)["nodes"])
        if entry["worker"]
    ]


def worker_command(lab, index, mode, functions):
    command = [*WORKER.split(), "--node", str(index), "--cluster", lab.cluster_path(mode)]
    if functions:
        command += ["--functions", *functions]
    return command


def start_workers(lab, node, mode, functions):
    """Run exactly the workers of `mode` that belong in `node`'s container; if any other worker
    runs there, stop every worker in it first."""
    commands = [
        worker_command(lab, index, mode, functions)
        for index, container in worker_nodes(lab, mode)
        if container == node
    ]
    if sorted(running_workers(lab, node)) == sorted(commands):
        print(f"node {node}: its {len(commands)} workers are already running")
        return
    stop_worker(lab, node)
    for command in commands:
        line = f"cd /app && exec {shlex.join(command)} >> /var/lib/ceph/worker.log 2>&1"
        daemon(lab, node, line)


def stop_worker(lab, node):
    if worker_running(lab, node):
        inside(lab, node, "pkill", "-f", "^" + WORKER)
        wait_for(lambda: not worker_running(lab, node), "worker stop")


def start_compute(lab, running):
    """Start the stopped compute-only containers and give their links the lab's current rate."""
    stopped = [node for node in lab.compute if lab.container(node) not in running]
    if not stopped:
        return
    docker("start", *[lab.container(node) for node in stopped])
    for node in stopped:
        wait_for(partial(network_ready, lab, node), "network setup")
        inside(lab, node, *tbf("eth0", shaped_mbit(lab)))


def stop_compute(lab, running):
    started = [lab.container(node) for node in lab.compute if lab.container(node) in running]
    if started:
        docker("stop", *started)


def network_ready(lab, node):
    """The entrypoint replaces itself with `sleep infinity` once network.py has set up the links.
    In the Ceph image `sleep` is a coreutils shebang script, so PID 1 then reads
    `/usr/bin/coreutils --coreutils-prog-shebang=sleep /usr/bin/sleep infinity`."""
    return inside(lab, node, "ps", "-o", "args=", "-p", "1").strip().endswith("sleep infinity")


def shape(lab, mbit):
    """Shape every running node's link and every router port to `mbit`, and remember the rate."""
    approved(lab)
    running = assert_owned(lab)
    if mbit <= 0:
        raise ValueError("bandwidth must be positive")
    for node in range(lab.nodes):
        if lab.container(node) in running:
            inside(lab, node, *tbf("eth0", mbit))
    for dev in json.loads(inside(lab, "router", "ip", "-j", "link")):
        if dev["ifname"].startswith("eth"):
            inside(lab, "router", *tbf(dev["ifname"], mbit))
    (lab.state / "shaped.json").write_text(json.dumps({"mbit": mbit}))


def shaped_mbit(lab):
    """The rate `shape` last set; containers shape themselves to `bandwidth_mbit` when they start."""
    record = lab.state / "shaped.json"
    return json.loads(record.read_text())["mbit"] if record.exists() else lab.bandwidth_mbit


def verify(lab, mode, mbit, bandwidth_probe="iperf-summary"):
    """Check every node that runs in `mode`: its links and routes, the placement of its pool if it
    has one, RADOS from node 0 and every compute-only node to every pool, and each node's
    shared bandwidth."""
    running = assert_owned(lab)
    nodes = lab.active(mode)
    stopped = [lab.container(node) for node in nodes if lab.container(node) not in running]
    if stopped:
        raise RuntimeError(f"{' '.join(stopped)} stopped; run `mode {mode}` first")
    save(lab, "ceph-status.json", ceph(lab, "status", "-f", "json"))
    save(lab, "ceph-osd-dump.json", ceph(lab, "osd", "dump", "-f", "json"))
    save(lab, "ceph-crush.json", ceph(lab, "osd", "crush", "dump", "-f", "json"))
    for node in nodes:
        verify_network(lab, node)
        if node in lab.storage:
            verify_pool(lab, node)
    clients = [0] if mode in COMPUTATIONAL_STORAGE else [0, *lab.compute]
    smoke = {
        node: json.loads(inside(lab, node, "python3", "-c", SMOKE, str(lab.storage_nodes)))
        for node in clients
    }
    save(lab, f"rados-smoke-{mode}.json", smoke)
    if bandwidth_probe == "boundary-window":
        from infra.probe_boundaries import measure

        rates = measure(lab, mode, mbit)
    elif bandwidth_probe == "iperf-summary":
        rates = iperf_rates(lab, mode)
    else:
        raise ValueError("unknown bandwidth validation method")
    require_bandwidth(rates, mbit)
    weakest = {
        direction: min(rates[node][direction] for node in rates)
        for direction in ("shared_tx", "shared_rx")
    }
    save(
        lab,
        f"validated-{mode}.json",
        {
            "fsid": identity(lab)["fsid"],
            "mode": mode,
            "nodes": list(nodes),
            "replicas": lab.replicas,
            "smoke": smoke,
            "aggregate_mbit": weakest,
            "per_node_mbit": rates,
            "time": time.time(),
            "system_benchmark": False,
            "bandwidth_probe": bandwidth_probe,
        },
    )
    print(json.dumps(rates, indent=2))


def check(lab, mode, mbit):
    """The light check after a mode switch, instead of a full verify: every link of `mode`'s
    nodes and every router port is shaped by tbf at `mbit`, the mode's workers answer, Ceph is
    not in HEALTH_ERR and every PG is active+clean."""
    running = assert_owned(lab)
    nodes = lab.active(mode)
    stopped = [lab.container(node) for node in nodes if lab.container(node) not in running]
    if stopped:
        raise RuntimeError(f"{' '.join(stopped)} stopped; run `mode {mode}` first")
    links = {(node, "eth0"): tbf_mbit(lab, node, "eth0") for node in nodes}
    for dev in json.loads(inside(lab, "router", "ip", "-j", "link")):
        if dev["ifname"].startswith("eth"):
            links["router", dev["ifname"]] = tbf_mbit(lab, "router", dev["ifname"])
    unshaped = [
        f"{lab.container(node)}:{dev}" for (node, dev), rate in links.items() if rate != mbit
    ]
    if unshaped:
        raise RuntimeError(f"not shaped by tbf at {mbit:g} Mbit/s: {' '.join(unshaped)}")
    urls = [node["worker"] for node in cluster_file(lab, mode)["nodes"] if node["worker"]]
    inside(lab, 0, "python3", "-c", WORKERS_READY, *urls)
    health = json.loads(ceph(lab, "health", "-f", "json"))["status"]
    if health == "HEALTH_ERR":
        raise RuntimeError(f"Ceph is in {health}")
    if not pgs_clean(json.loads(ceph(lab, "pg", "stat", "-f", "json"))):
        raise RuntimeError("not every placement group is active+clean")
    save(
        lab,
        f"checked-{mode}.json",
        {"mode": mode, "nodes": list(nodes), "mbit": mbit, "health": health, "time": time.time()},
    )
    print(
        f"{lab.name} mode {mode}: {len(links)} links at {mbit:g} Mbit/s, {len(urls)} workers, {health}"
    )


def tbf_mbit(lab, node, dev):
    """The rate in Mbit/s of the root tbf qdisc on `dev` of `node`, or None if it has none."""
    for qdisc in json.loads(inside(lab, node, "tc", "-j", "qdisc", "show", "dev", dev)):
        if qdisc.get("root") and qdisc["kind"] == "tbf":
            return qdisc["options"]["rate"] * 8 / 1e6
    return None


def verify_network(lab, node):
    for args, filename in [
        (("ip", "-j", "addr"), "addresses"),
        (("ip", "-j", "route"), "routes"),
        (("tc", "-s", "-j", "qdisc"), "qdisc"),
    ]:
        save(lab, f"n{node}-{filename}.json", inside(lab, node, *args))
    links = json.loads(inside(lab, node, "ip", "-j", "link"))
    if {x["ifname"] for x in links if "UP" in x["flags"]} != {"lo", "eth0", "lab"}:
        raise RuntimeError(f"unexpected active interface on node {node}")
    for target in range(lab.nodes):
        route = json.loads(inside(lab, node, "ip", "-j", "route", "get", lab.address(target)))[0]
        if target == node and route["dev"] != "lo":
            raise RuntimeError("local OSD does not stay local")
        if target != node and (route["dev"] != "lab" or route.get("gateway") != f"10.77.{node}.2"):
            raise RuntimeError("inter-node route bypasses router")


def verify_pool(lab, node):
    pool = f"node{node}"
    settings = json.loads(ceph(lab, "osd", "pool", "get", pool, "all", "-f", "json"))
    if settings["size"] != lab.replicas or settings["min_size"] != 1:
        raise RuntimeError(f"wrong replication settings for {pool}: {settings}")
    osds = lab.replica_osds(node)
    for key in ("smoke/a", "smoke/b", "smoke/c"):
        validate_placement(osds, json.loads(ceph(lab, "osd", "map", pool, key, "-f", "json")))
    listing = json.loads(ceph(lab, "pg", "ls-by-pool", pool, "-f", "json"))
    groups = listing["pg_stats"] if isinstance(listing, dict) else listing
    if len(groups) != PG_COUNT:
        raise RuntimeError(f"{pool} has {len(groups)} placement groups, expected {PG_COUNT}")
    for group in groups:
        validate_placement(osds, group)


def iperf_rates(lab, mode):
    """Each node of `mode` in turn sends two flows at once (shared TX), then receives two (shared RX).
    A flow's rate is its sender's: behind tbf, one receiver of a pair often closes its summary
    about 10 s after the sender stops (seen on aakashpc as 46 + 242 Mbit on a link whose byte
    counters showed 378), while the senders' rates agree with the boundary-window probe."""
    ring = lab.active(mode)
    measured = {}
    rates = {}
    try:
        start_iperf_servers(lab, ring)
        with ThreadPoolExecutor(max_workers=2) as executor:
            for node in ring:
                rates[node] = {}
                for direction, flows in ring_flows(ring, node).items():
                    futures = [executor.submit(iperf, lab, *flow) for flow in flows]
                    rows = [future.result() for future in futures]
                    measured[f"n{node}-{direction}"] = rows
                    rates[node][direction] = (
                        sum(x["end"]["sum_sent"]["bits_per_second"] for x in rows) / 1e6
                    )
    finally:
        for node in ring:
            inside(lab, node, "pkill", "-x", "iperf3")
    save(lab, f"bandwidth-{mode}.json", measured)
    return rates


def start_iperf_servers(lab, ring):
    """Run iperf3 servers on ports 5201 and 5202 of every node in `ring` as `docker exec`
    processes, which Docker reaps once `pkill` ends them, and wait until they listen.
    `iperf3 -D` would hand them to PID 1, `sleep`, which never reaps them, so each verify left
    two zombies per node that count against the container's pids limit."""
    for node in ring:
        for port in (5201, 5202):
            inside(lab, node, "iperf3", "-s", "-p", str(port), detached=True)
    for node in ring:
        wait_for(partial(iperf_listening, lab, node), "iperf3 servers", 30)


def iperf_listening(lab, node):
    sockets = inside(lab, node, "ss", "-Hltn")
    return all(f":{port} " in sockets for port in (5201, 5202))


def iperf(lab, source, destination, port):
    args = ["iperf3", "-c", lab.address(destination), "-p", str(port), "-t", "5", "-O", "1", "-J"]
    return json.loads(inside(lab, source, *args))


def stop(lab):
    assert_owned(lab)
    print(docker("stop", *lab.containers))


def write_cluster_files(lab):
    for mode in lab.modes:
        path = ROOT / lab.cluster_path(mode)
        if path.exists() and json.loads(path.read_text()) == cluster_file(lab, mode):
            print(f"{lab.cluster_path(mode)} is up to date")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cluster_file(lab, mode), indent=2) + "\n")
        print(f"wrote {lab.cluster_path(mode)}")


def describe(lab, nodes):
    return f"{lab.service(nodes[0])}-{lab.service(nodes[-1])}" if nodes else "none"


def plan(lab, cpus, memory, free_disk):
    """Print what the lifecycle would create and run; return the budget problems."""
    validate_compose(lab, compose(lab))
    lines, problems = budget_report(lab, cpus, memory, free_disk)
    print(
        f"Lab {lab.name}: {lab.storage_nodes} storage nodes ({describe(lab, lab.storage)}), "
        f"{lab.compute_nodes} compute-only nodes ({describe(lab, lab.compute)}), image {lab.image}, "
        f"{copies(lab)}"
    )
    print("\n".join(lines))
    print("\nContainers:", " ".join(lab.containers))
    print("Volumes:   ", " ".join(lab.volumes))
    for node in range(lab.nodes):
        addresses = f"node {lab.address(node)}, router {lab.router_address(node)}"
        print(f"Network {lab.networks[node]} {lab.subnet(node)}: {addresses}")
    print("\nceph.conf of node 0 (each storage node has its own [osd] addresses):")
    print(ceph_conf(lab, "<fsid>", 0))
    if lab.replicas > 1:
        print("CRUSH rules added as text:")
        print(crush_rules(lab))
    print("Pool commands:")
    for args in pool_commands(lab):
        print("  ceph", shlex.join(args))
    print("\nPlacement checked by verify (pool: up = acting, primary first):")
    for node in lab.storage:
        print(f"  node{node}: {lab.replica_osds(node)}")
    print(f"\nShaping at {lab.bandwidth_mbit:g} Mbit/s, on every node and router interface:")
    print("  ", shlex.join(tbf("eth0", lab.bandwidth_mbit)))
    for mode in lab.modes:
        print(
            f"\nMode {mode}: containers {describe(lab, lab.active(mode))} and the router run, "
            f"workers on {describe(lab, lab.workers(mode))}; cluster file {lab.cluster_path(mode)}:"
        )
        print(json.dumps(cluster_file(lab, mode), indent=2))
    if problems:
        print("\nThis lab does not fit the host.")
    else:
        print("\nConfiguration passes isolation and budget checks.")
        print("Deployment requires explicit approval.")
    return problems


def main():
    steps = {"build": build, "up": up, "bootstrap": bootstrap, "resume": resume, "stop": stop}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["plan", "cluster", "mode", "stop-workers", "shape", "verify", "check", *steps],
    )
    parser.add_argument(
        "value",
        nargs="?",
        help=f"mode, verify and check: {', '.join(MODES)}; shape: Mbit/s (default: last)",
    )
    parser.add_argument(
        "--lab", type=Path, required=True, help="lab config, e.g. infra/labs/cephdag8.json"
    )
    parser.add_argument(
        "--mbit", type=float, help="verify and check: rate; defaults to the rate last shaped"
    )
    parser.add_argument(
        "--probe", choices=["iperf-summary", "boundary-window"], default="iperf-summary"
    )
    parser.add_argument(
        "--functions", nargs="*", default=[], help="mode: extra function modules workers load"
    )
    parser.add_argument(
        "--host",
        nargs=3,
        type=float,
        metavar=("CPUS", "MEMORY_GIB", "FREE_DISK_GIB"),
        help="plan: budget against this host instead of this machine and /var/lib/docker",
    )
    args = parser.parse_args()
    if args.action in ("mode", "verify", "check") and args.value not in MODES:
        parser.error(f"{args.action} needs one of {', '.join(MODES)}")
    lab = Lab.load(args.lab)
    if args.action == "plan":
        if args.host:
            host = (args.host[0], args.host[1] * GIB, args.host[2] * GIB)
        else:
            host = host_resources("/var/lib/docker" if Path("/var/lib/docker").exists() else "/")
        raise SystemExit(1 if plan(lab, *host) else 0)
    elif args.action == "cluster":
        write_cluster_files(lab)
    elif args.action == "mode":
        switch_mode(lab, args.value, args.functions)
    elif args.action == "stop-workers":
        stop_workers(lab)
    elif args.action == "shape":
        shape(lab, float(args.value) if args.value else shaped_mbit(lab))
    elif args.action == "verify":
        verify(lab, args.value, args.mbit or shaped_mbit(lab), args.probe)
    elif args.action == "check":
        check(lab, args.value, args.mbit or shaped_mbit(lab))
    else:
        steps[args.action](lab)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))  # for the boundary-window probe when run as infra/lab.py
    main()
