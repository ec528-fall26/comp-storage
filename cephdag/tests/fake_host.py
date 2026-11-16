"""Stand-ins for Docker and for a container's `ip`, so lifecycle code runs without either.

The same fakes record the original three-node lifecycle (tests/fixtures/make_lab_fixture.py)
and the current one (tests/test_infra.py), so the two command traces can be compared.
"""

from contextlib import contextmanager
import itertools
import json
import os
from pathlib import Path
import re
from unittest import mock
import uuid

DECOMPILED_CRUSH = """# begin crush map
# rules
rule replicated_rule {
\tid 0
\ttype replicated
\tstep take default
\tstep chooseleaf firstn 0 type host
\tstep emit
}

# end crush map
"""


@contextmanager
def deterministic():
    """Fixed uuids and clock, and no sleeping, while lifecycle code runs."""
    uuids = (uuid.UUID(int=index) for index in itertools.count(1))
    with (
        mock.patch("uuid.uuid4", side_effect=lambda: next(uuids)),
        mock.patch("time.time", return_value=1_800_000_000.0),
        mock.patch("time.sleep"),
    ):
        yield


class FakeDocker:
    """Answers the docker commands of infra/lab.py and records each one in `trace`.

    It keeps what the commands created: compose labels, running containers and the command line
    of each worker in them, files written into containers, CRUSH rules and pools. Placement follows the
    `step take` buckets of each pool's rule. Like Docker, it refuses to exec in a stopped container.
    """

    def __init__(self, state, containers=(), volumes=(), networks=()):
        self.state = str(state)
        self.existing = {"containers": list(containers), "volumes": list(volumes)}
        self.docker_networks = [
            {"Name": "bridge", "IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}}
        ]
        self.docker_networks += [
            {"Name": name, "IPAM": {"Config": [{"Subnet": subnet}]}} for name, subnet in networks
        ]
        self.trace = []
        self.composes = []
        self.labels = {}
        self.running = set()
        self.workers = {}
        self.addresses = {}
        self.numbers = {}
        self.osds = []
        self.files = {}
        self.rules = {}
        self.pools = {}
        self.placement_override = {}
        self.flow_mbit = {}
        # Root tbf rate in Mbit/s of each (container, device); a container loses it when it stops.
        self.qdiscs = {}
        self.health = "HEALTH_WARN"
        self.pg_states = ["active+clean"]
        # Flows whose receiver closes its summary late, so that it reports a third of the rate.
        self.late_receivers = set()
        # PID 1 of a container once its entrypoint has set up the network, as in the Ceph image.
        self.pid1 = "/usr/bin/coreutils --coreutils-prog-shebang=sleep /usr/bin/sleep infinity"

    def __call__(self, args, *, data=None, timeout=120):
        self.trace.append(
            {
                "args": [arg.replace(self.state, "<state>") for arg in args],
                "data": None if data is None else data.decode(),
            }
        )
        if args[:2] == ["docker", "exec"]:
            rest = args[2:]
            while rest[0] in ("-i", "-d"):
                rest = rest[1:]
            if rest[0] not in self.running:
                raise RuntimeError(f"container {rest[0]} is not running")
            return self.execute(rest[0], rest[1:], data)
        return self.host(args[1:])

    @property
    def nodes(self):
        return len(self.addresses)

    def host(self, args):
        if args[:2] == ["ps", "-a"]:
            return "\n".join(self.existing["containers"])
        if args[:2] == ["volume", "ls"]:
            return "\n".join(self.existing["volumes"])
        if args[:3] == ["network", "ls", "-q"]:
            return " ".join(f"id{index}" for index in range(len(self.docker_networks)))
        if args[:2] == ["network", "inspect"]:
            return json.dumps([self.docker_networks[int(name[2:])] for name in args[2:]])
        if args[0] == "info":
            return "/var/lib/docker\n"
        if args[:2] == ["compose", "-f"] and args[3:] == ["up", "-d", "--no-build"]:
            config = json.loads(Path(args[2]).read_text())
            self.composes.append(config)
            for service in config["services"].values():
                self.labels[service["container_name"]] = service["labels"]
                self.running.add(service["container_name"])
                if service["environment"]["ROLE"] == "node":
                    (address,) = [link["ipv4_address"] for link in service["networks"].values()]
                    self.addresses[service["container_name"]] = address
                    self.numbers[service["container_name"]] = int(service["environment"]["NODE"])
            return ""
        if args[0] == "inspect":
            labels = self.labels.get(args[1], {})
            running = args[1] in self.running
            return json.dumps([{"Config": {"Labels": labels}, "State": {"Running": running}}])
        if args[0] == "start":
            self.running.update(args[1:])
            return ""
        if args[0] == "stop":
            self.running.difference_update(args[1:])
            for container in args[1:]:
                self.workers.pop(container, None)
                for key in [key for key in self.qdiscs if key[0] == container]:
                    del self.qdiscs[key]
            return ""
        if args[0] == "build":
            return ""
        raise AssertionError(f"unexpected docker command: {args}")

    def execute(self, container, args, data):
        if args[0] == "find" or args[0] in (
            "ceph-authtool",
            "monmaptool",
            "mkdir",
            "ceph-mon",
            "ceph-osd",
        ):
            return ""
        if args[:2] == ["bash", "-c"] and args[2].startswith("umask 077; cat > "):
            self.files[container, args[2][len("umask 077; cat > ") :]] = data.decode()
            return ""
        worker = re.search(r"exec (python3 -m cephdag\.worker .*?) >>", " ".join(args))
        if args[:2] == ["bash", "-c"] and worker:
            self.workers.setdefault(container, []).append(worker.group(1))
            return ""
        if args == ["pkill", "-f", "^python3 -m cephdag.worker"]:
            self.workers.pop(container)
            return ""
        if args[:3] == ["tc", "qdisc", "replace"] and args[6] == "tbf":
            self.qdiscs[container, args[4]] = float(args[8].removesuffix("mbit"))
            return ""
        if args[:4] == ["tc", "-j", "qdisc", "show"]:
            if (container, args[5]) not in self.qdiscs:
                return json.dumps([{"kind": "noqueue", "root": True, "options": {}}])
            rate = self.qdiscs[container, args[5]] * 1e6 / 8
            return json.dumps([{"kind": "tbf", "root": True, "options": {"rate": rate}}])
        if args[:2] == ["bash", "-c"] or args[0] in ("tc", "pkill"):
            return ""
        if args[0] == "cat":
            return self.files.get((container, args[1]), "KEY\n")
        if args[0] == "ceph":
            return self.ceph(args[1:])
        if args[:2] == ["crushtool", "-d"]:
            return DECOMPILED_CRUSH
        if args[:2] == ["crushtool", "-c"]:
            return ""
        if args == ["ps", "-eo", "args"] and container in self.workers:
            return "COMMAND\n" + "".join(f"{line}\n" for line in self.workers[container])
        if args[:2] == ["ps", "-eo"]:
            return "COMMAND\n"
        if args == ["ps", "-o", "args=", "-p", "1"]:
            return self.pid1 + "\n"
        if args[:3] == ["ip", "-j", "link"]:
            return json.dumps(self.links(container))
        if args[:4] == ["ip", "-j", "route", "get"]:
            if self.addresses[container] == args[4]:
                return json.dumps([{"dst": args[4], "dev": "lo"}])
            node = self.numbers[container]
            return json.dumps([{"dst": args[4], "dev": "lab", "gateway": f"10.77.{node}.2"}])
        if args[:3] in (["ip", "-j", "addr"], ["ip", "-j", "route"]) or args[:3] == [
            "tc",
            "-s",
            "-j",
        ]:
            return "[]"
        if args[:2] == ["python3", "-c"] and "/state" in args[2]:
            listening = {
                served_url(command) for commands in self.workers.values() for command in commands
            }
            if not set(args[3:]) <= listening:
                raise RuntimeError(f"no worker answers at {set(args[3:]) - listening}")
            return ""
        if args[:2] == ["python3", "-c"]:
            return json.dumps(
                [{"node": n, "bytes": 1048576, "sha256": "0" * 64} for n in range(int(args[3]))]
            )
        if args[0] == "iperf3" and args[1] == "-s":
            return ""
        if args == ["ss", "-Hltn"]:
            return "".join(f"LISTEN 0      5      *:{port}      *:*\n" for port in (5201, 5202))
        if args[0] == "iperf3" and args[1] == "-c":
            mbit = self.flow_mbit.get((container, args[2]), 50)
            received = mbit / 3 if (container, args[2]) in self.late_receivers else mbit
            return json.dumps(
                {
                    "end": {
                        "sum_sent": {"bits_per_second": mbit * 1e6},
                        "sum_received": {"bits_per_second": received * 1e6},
                    }
                }
            )
        raise AssertionError(f"unexpected command in {container}: {args}")

    def links(self, container):
        if container.endswith("-router"):
            return [{"ifname": "lo", "flags": ["UP"]}] + [
                {"ifname": f"eth{index}", "flags": ["UP"]} for index in range(self.nodes)
            ]
        return [{"ifname": name, "flags": ["UP"]} for name in ("lo", "eth0", "lab")]

    def ceph(self, args):
        if args == ["status", "-f", "json"]:
            conf = next(
                text for (_, path), text in self.files.items() if path == "/etc/ceph/ceph.conf"
            )
            return json.dumps({"fsid": re.search(r"^fsid = (\S+)$", conf, re.MULTILINE).group(1)})
        if args == ["status"]:
            return "HEALTH_OK\n"
        if args[0] == "auth" or args[:3] == ["osd", "crush", "add"] or args[:2] == ["osd", "set"]:
            return ""
        if args[:2] == ["osd", "create"]:
            self.osds.append(int(args[3]))
            return args[3] + "\n"
        if args == ["osd", "dump", "-f", "json"]:
            return json.dumps({"osds": [{"osd": n, "up": 1, "in": 1} for n in self.osds]})
        if args[:3] == ["osd", "crush", "dump"]:
            return "{}"
        if args[:4] == ["osd", "crush", "rule", "create-replicated"]:
            self.rules[args[4]] = [int(args[5][1:])]
            return ""
        if args[:2] in (["osd", "getcrushmap"], ["osd", "setcrushmap"]):
            if args[1] == "setcrushmap":
                self.install_rules()
            return ""
        if args[:3] == ["osd", "pool", "create"]:
            self.pools[args[3]] = {"rule": args[7], "size": 1, "min_size": 1}
            return ""
        if args[:3] == ["osd", "pool", "set"]:
            self.pools[args[3]][args[4]] = int(args[5])
            return ""
        if args[:3] == ["osd", "pool", "application"]:
            return ""
        if args[:3] == ["osd", "pool", "get"]:
            return json.dumps({key: self.pools[args[3]][key] for key in ("size", "min_size")})
        if args[:2] == ["osd", "map"]:
            return json.dumps(self.placement(args[2]))
        if args[:2] == ["pg", "ls-by-pool"]:
            groups = [dict(self.placement(args[2]), pgid=f"x.{index:x}") for index in range(32)]
            return json.dumps({"pg_ready": True, "pg_stats": groups})
        if args == ["pg", "stat", "-f", "json"]:
            states = [{"name": state, "num": 1} for state in self.pg_states]
            return json.dumps({"pg_summary": {"num_pg_by_state": states}})
        if args == ["health", "-f", "json"]:
            return json.dumps({"status": self.health})
        raise AssertionError(f"unexpected ceph command: {args}")

    def install_rules(self):
        text = next(text for (_, path), text in self.files.items() if path == "/tmp/crush.txt")
        for name, body in re.findall(r"rule (\w+) \{(.*?)\}", text, re.DOTALL):
            self.rules[name] = [int(osd) for osd in re.findall(r"step take n(\d+)", body)]

    def placement(self, pool):
        if pool in self.placement_override:
            return self.placement_override[pool]
        osds = self.rules[self.pools[pool]["rule"]][: self.pools[pool]["size"]]
        return {"up": osds, "up_primary": osds[0], "acting": osds, "acting_primary": osds[0]}


def served_url(command):
    """The URL a worker started with `command` listens on: its node's entry in the committed
    cluster file it names, as cephdag.worker reads it."""
    words = command.split()
    cluster = json.loads(Path(words[words.index("--cluster") + 1]).read_text())
    return cluster["nodes"][int(words[words.index("--node") + 1])]["worker"]


class FakeNamespace:
    """Answers network.py's `ip -j` queries for one container and records the commands it runs."""

    def __init__(self, addresses):
        self.addresses = addresses
        self.commands = []

    def check_output(self, args):
        if args == ["ip", "-j", "addr"]:
            return json.dumps(self.addresses).encode()
        if args == ["ip", "-j", "rule"]:
            return b"[]"
        if args == ["ip", "-j", "link"]:
            return json.dumps([{"ifname": x["ifname"]} for x in self.addresses]).encode()
        raise AssertionError(f"unexpected query: {args}")

    def run(self, args, check):
        self.commands.append(list(args))


def network_commands(network, config):
    """The commands `network.main()` runs at start in every container of a compose project."""
    commands = {}
    for name, service in config["services"].items():
        addresses = [{"ifname": "lo", "addr_info": [{"local": "127.0.0.1"}]}]
        links = list(service["networks"].values())
        # Docker names a container's interfaces in attach order; reversing checks they are found by address.
        for index, link in enumerate(reversed(links)):
            addresses.append(
                {"ifname": f"eth{index}", "addr_info": [{"local": link["ipv4_address"]}]}
            )
        namespace = FakeNamespace(addresses)
        with (
            mock.patch.dict(os.environ, service["environment"], clear=True),
            mock.patch.object(network.subprocess, "check_output", namespace.check_output),
            mock.patch.object(network.subprocess, "run", namespace.run),
        ):
            network.main()
        commands[name] = namespace.commands
    return commands
