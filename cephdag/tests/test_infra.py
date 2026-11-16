import contextlib
import copy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from cephdag.cluster import Cluster
from cephdag.policies import native
from infra import lab as lifecycle
from infra import network
from infra.lab import (
    GIB,
    Lab,
    budget,
    cluster_file,
    compose,
    crush_rules,
    pgs_clean,
    require_fresh,
    validate_compose,
    validate_placement,
)
from tests.fake_host import FakeDocker, deterministic, network_commands

LABS = Path("infra/labs")
FIXTURE = json.loads(Path("tests/fixtures/lab-3node.json").read_text())
EXISTING = Lab.load(LABS / "cephdag.json")
EIGHT = Lab.load(LABS / "cephdag8.json")
EIGHT_REPLICATED = replace(EIGHT, name="cephdag8r", replicas=2, first_subnet="172.30.160.0/24")
STORAGE = [f"cephdag8-n{n}" for n in range(8)]
COMPUTE = [f"cephdag8-c{j}" for j in range(8)]
AAKASHPC = (32, 123 * GIB, 1126 * GIB)
REQUIRE_RESULTS_DIR = lifecycle.require_results_dir
EXISTING_RESOURCES = {
    "containers": ["cephdag-n0", "cephdag-n1", "cephdag-n2", "cephdag-router"],
    "volumes": ["cephdag_osd0", "cephdag_osd1", "cephdag_osd2"],
    "networks": [(f"cephdag_link{n}", f"172.30.{80 + n}.0/24") for n in range(3)],
}
DOCKER_ROOT_QUERY = ["docker", "info", "--format", "{{.DockerRootDir}}"]


@contextlib.contextmanager
def fake_host(lab, approve=True, host=AAKASHPC, **existing):
    """A temporary checkout whose Docker is a FakeDocker holding `existing` resources.

    The results directory counts as present; ResultsDirectoryTests check the real test.
    """
    with (
        tempfile.TemporaryDirectory() as directory,
        mock.patch.object(lifecycle, "ROOT", Path(directory)),
    ):
        if approve:
            lab.state.mkdir(parents=True)
            (lab.state / "approval.json").write_text('{"approved": true}')
        docker = FakeDocker(lab.state, **existing)
        with (
            mock.patch.object(lifecycle, "command", docker),
            mock.patch.object(lifecycle, "host_resources", return_value=host),
            mock.patch.object(lifecycle, "require_results_dir"),
            deterministic(),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            yield docker


def create(lab):
    lifecycle.up(lab)
    lifecycle.bootstrap(lab)


def full_lifecycle(lab):
    create(lab)
    lifecycle.resume(lab)
    lifecycle.shape(lab, 400.0)
    lifecycle.stop(lab)


def executions(trace):
    """(container, command) of every `docker exec` in `trace`."""
    for entry in trace:
        if entry["args"][1] == "exec":
            rest = entry["args"][2:]
            while rest[0] in ("-i", "-d"):
                rest = rest[1:]
            yield rest[0], rest[1:]


def worker_commands(trace):
    return [entry["args"] for entry in trace if "cephdag.worker" in " ".join(entry["args"])]


def tbf_targets(trace, rate):
    return [
        (entry["args"][2], entry["args"][7])
        for entry in trace
        if "tbf" in entry["args"] and f"{rate}mbit" in entry["args"]
    ]


class ThreeNodeIdentityTests(unittest.TestCase):
    """The three-node config reproduces the original lab, recorded in tests/fixtures/lab-3node.json."""

    def test_runs_the_original_commands_plus_one_docker_root_query(self):
        with fake_host(EXISTING) as docker:
            full_lifecycle(EXISTING)
        added = [entry for entry in docker.trace if entry["args"] == DOCKER_ROOT_QUERY]
        self.assertEqual(len(added), 1)
        self.assertEqual(
            [entry for entry in docker.trace if entry not in added], FIXTURE["lifecycle"]
        )

    def test_compose_differs_only_by_the_subnet_list(self):
        with fake_host(EXISTING) as docker:
            lifecycle.up(EXISTING)
        deployed = docker.composes[0]
        subnets = {
            service["environment"].pop("SUBNETS") for service in deployed["services"].values()
        }
        self.assertEqual(subnets, {"172.30.80.0/24,172.30.81.0/24,172.30.82.0/24"})
        self.assertEqual(deployed, FIXTURE["compose"])

    def test_container_network_setup_is_unchanged(self):
        self.assertEqual(network_commands(network, compose(EXISTING)), FIXTURE["network"])

    def test_cs_cluster_file_is_the_lab_cluster_file(self):
        self.assertEqual(EXISTING.modes, ("cs",))
        self.assertEqual(
            cluster_file(EXISTING, "cs"), json.loads(Path("cephdag/lab-cluster.json").read_text())
        )


class LabConfigTests(unittest.TestCase):
    def test_committed_labs_coexist_and_their_cluster_files_are_current(self):
        labs = [Lab.load(path) for path in sorted(LABS.glob("*.json"))]
        self.assertEqual({lab.name for lab in labs}, {"cephdag", "cephdag8"})
        subnets = [lab.subnet(node) for lab in labs for node in range(lab.nodes)]
        for index, subnet in enumerate(subnets):
            self.assertFalse(any(subnet.overlaps(other) for other in subnets[index + 1 :]), subnet)
        names = [name for lab in labs for name in lab.containers + lab.volumes + lab.networks]
        self.assertEqual(len(names), len(set(names)))
        for lab in labs:
            self.assertEqual(budget(lab, *AAKASHPC)[1], [], lab.name)
        for mode in EIGHT.modes:
            committed = json.loads(Path(EIGHT.cluster_path(mode)).read_text())
            self.assertEqual(committed, cluster_file(EIGHT, mode), mode)

    def test_cluster_files_share_the_storage_pools_and_differ_in_who_computes(self):
        cs = cluster_file(EIGHT, "cs")
        disagg = cluster_file(EIGHT, "disagg")
        self.assertEqual(
            cs["nodes"],
            [{"pool": f"node{n}", "worker": f"http://172.30.{144 + n}.10:8080"} for n in range(8)],
        )
        self.assertEqual(
            disagg["nodes"],
            [{"pool": f"node{n}", "worker": None} for n in range(8)]
            + [{"pool": None, "worker": f"http://172.30.{144 + n}.10:8080"} for n in range(8, 16)],
        )
        for config in (cs, disagg):
            self.assertEqual(
                {key: config[key] for key in ("metadata_pool", "ceph_conf", "namespace")},
                {
                    "metadata_pool": "node0",
                    "ceph_conf": "/etc/ceph/ceph.conf",
                    "namespace": "cephdag",
                },
            )
        cluster = Cluster.from_dict(cs)
        self.assertEqual(cluster.pools, tuple(f"node{n}" for n in range(8)))
        self.assertEqual(cluster.nodes[7].address, ("172.30.151.10", 8080))

    def test_disagg2_lists_each_compute_containers_second_worker_as_one_more_node(self):
        disagg = cluster_file(EIGHT, "disagg")
        disagg2 = cluster_file(EIGHT, "disagg2")
        self.assertEqual(disagg2["nodes"][:16], disagg["nodes"])
        self.assertEqual(
            disagg2["nodes"][16:],
            [{"pool": None, "worker": f"http://172.30.{152 + j}.10:8081"} for j in range(8)],
        )
        self.assertEqual(
            {key: disagg2[key] for key in ("metadata_pool", "ceph_conf", "namespace")},
            {key: disagg[key] for key in ("metadata_pool", "ceph_conf", "namespace")},
        )
        cluster = Cluster.from_dict(disagg2)
        self.assertEqual(cluster.storage_nodes, tuple(range(8)))
        self.assertEqual(cluster.worker_nodes, tuple(range(8, 24)))
        self.assertEqual(
            lifecycle.worker_nodes(EIGHT, "disagg2"),
            [(n, n) for n in range(8, 16)] + [(n, n - 8) for n in range(16, 24)],
        )

    def test_cs2_lists_each_storage_containers_second_worker_as_one_more_node_on_its_host(self):
        cs = cluster_file(EIGHT, "cs")
        cs2 = cluster_file(EIGHT, "cs2")
        self.assertEqual(cs2["nodes"][:8], cs["nodes"])
        self.assertEqual(
            cs2["nodes"][8:],
            [{"pool": None, "worker": f"http://172.30.{144 + n}.10:8081"} for n in range(8)],
        )
        self.assertEqual(
            {key: cs2[key] for key in ("metadata_pool", "ceph_conf", "namespace")},
            {key: cs[key] for key in ("metadata_pool", "ceph_conf", "namespace")},
        )
        cluster = Cluster.from_dict(cs2)
        self.assertEqual(cluster.storage_nodes, tuple(range(8)))
        self.assertEqual(cluster.worker_nodes, tuple(range(16)))
        self.assertEqual(cluster.hosts, (*range(8), *range(8)))
        self.assertEqual(cluster.interface_nodes, tuple(range(8)))
        self.assertEqual(cluster.journal_pool(11), "node3")
        self.assertEqual(
            lifecycle.worker_nodes(EIGHT, "cs2"),
            [(n, n) for n in range(8)] + [(n, n - 8) for n in range(8, 16)],
        )

    def test_rejects_invalid_configs(self):
        fields = json.loads((LABS / "cephdag8.json").read_text())
        for change in [
            {"name": "cephdag-8"},
            {"storage_nodes": 2},
            {"compute_nodes": -1},
            {"storage_nodes": 200, "compute_nodes": 57},
            {"replicas": 3},
            {"worker_slots": 2},
            {"worker_cpus": 0},
            {"osd_memory_mib": 0},
            {"first_subnet": "172.30.144.0/23"},
            {"first_subnet": "8.8.8.0/24"},
            {"first_subnet": "172.31.250.0/24"},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                Lab(**{**fields, **change})
        with self.assertRaises(TypeError):
            Lab(**fields, nodes=16)

    def test_storage_nodes_come_first_and_only_they_hold_data(self):
        self.assertEqual(EIGHT.containers, STORAGE + COMPUTE + ["cephdag8-router"])
        self.assertEqual(EIGHT.volumes, [f"cephdag8_osd{n}" for n in range(8)])
        self.assertEqual(EIGHT.address(15), "172.30.159.10")
        self.assertEqual(EIGHT.router_address(15), "172.30.159.254")
        self.assertEqual((EIGHT.cpus(7), EIGHT.memory_mib(7)), (3, 6144))
        self.assertEqual((EIGHT.cpus(8), EIGHT.memory_mib(8)), (2, 3072))
        self.assertEqual(EIGHT.osd_memory_target, 2576980377)
        self.assertEqual(EIGHT.replica_osds(7), [7])
        self.assertEqual(EIGHT_REPLICATED.replica_osds(7), [7, 0])
        self.assertEqual(EIGHT.results_dir, "/mnt/mass/cephdag")


class BudgetTests(unittest.TestCase):
    def test_recommended_lab_fits_aakashpc_with_equal_cpu_in_both_modes(self):
        use, problems = budget(EIGHT, *AAKASHPC)
        self.assertEqual(problems, [])
        self.assertEqual(
            use["cpus"], {"cs": 24.25, "disagg": 24.25, "disagg2": 24.25, "cs2": 24.25}
        )
        self.assertEqual(use["memory"], (8 * 6144 + 8 * 3072 + 128) * 2**20)
        self.assertEqual(use["osd_disk"], 448 * GIB)

    def test_refuses_osds_above_the_ssd_cap_whatever_the_free_disk(self):
        roomy = (32, 123 * GIB, 10_000 * GIB)
        self.assertEqual(budget(replace(EIGHT, osd_gib=57), *roomy)[1], [])
        (problem,) = budget(replace(EIGHT, osd_gib=58), *roomy)[1]
        self.assertIn("OSD sizes 464 GiB exceed the 460 GiB of SSD", problem)

    def test_refuses_what_exceeds_the_host(self):
        problems = budget(EIGHT, 24, 123 * GIB, 1126 * GIB)[1]
        self.assertEqual(len(problems), 4)
        self.assertIn("cs2 mode uses 24.25 CPUs", problems[3])
        disagg, disagg2 = budget(replace(EIGHT, compute_nodes=12), *AAKASHPC)[1]
        self.assertIn("disagg mode uses 32.25 CPUs", disagg)
        self.assertIn("disagg2 mode uses 32.25 CPUs", disagg2)
        self.assertIn("memory", budget(EIGHT, 32, 80 * GIB, 1126 * GIB)[1][0])
        # 448 GiB of OSDs needs 528 GiB free with the 80 GiB reserve.
        self.assertEqual(budget(EIGHT, 32, 123 * GIB, 528 * GIB)[1], [])
        self.assertIn("free disk", budget(EIGHT, 32, 123 * GIB, 527 * GIB)[1][0])

    def test_refuses_two_workers_per_container_without_1024_mib_of_worker_memory_each(self):
        self.assertEqual(budget(replace(EIGHT, worker_memory_mib=2048), *AAKASHPC)[1], [])
        problems = budget(replace(EIGHT, worker_memory_mib=2047), *AAKASHPC)[1]
        self.assertEqual(
            problems,
            [
                f"{mode} mode runs 2 workers in 2047 MiB of worker memory, less than 1024 MiB each"
                for mode in ("disagg2", "cs2")
            ],
        )
        self.assertEqual(len(budget(replace(EIGHT, worker_memory_mib=1023), *AAKASHPC)[1]), 4)

    def test_plan_prints_the_lab_without_docker_and_reports_misfits(self):
        output = io.StringIO()
        with mock.patch.object(lifecycle, "command", side_effect=AssertionError("ran a command")):
            with contextlib.redirect_stdout(output):
                self.assertEqual(lifecycle.plan(EIGHT_REPLICATED, *AAKASHPC), [])
                self.assertEqual(len(lifecycle.plan(replace(EIGHT, osd_gib=60), *AAKASHPC)), 1)
        text = output.getvalue()
        self.assertIn("190 GiB of inputs and outputs", text)
        self.assertIn("container quotas 40.25", text)
        self.assertIn("Mode disagg: containers n0-c7 and the router run, workers on c0-c7", text)
        self.assertIn(
            "\tstep take n7\n\tstep choose firstn 1 type osd\n\tstep emit\n\tstep take n0\n", text
        )
        self.assertIn("REFUSED: OSD sizes 480 GiB exceed the 460 GiB of SSD", text)

    def test_up_refuses_a_lab_that_does_not_fit(self):
        with fake_host(EIGHT, host=(32, 123 * GIB, 500 * GIB)) as docker:
            with self.assertRaisesRegex(RuntimeError, "does not fit"):
                lifecycle.up(EIGHT)
        self.assertEqual(docker.composes, [])


class ResultsDirectoryTests(unittest.TestCase):
    def test_must_be_an_existing_directory_this_user_can_write(self):
        with tempfile.TemporaryDirectory() as directory:
            REQUIRE_RESULTS_DIR(replace(EIGHT, results_dir=directory))
            with self.assertRaisesRegex(RuntimeError, "does not exist; mount the archive disk"):
                REQUIRE_RESULTS_DIR(replace(EIGHT, results_dir=f"{directory}/missing"))
            readonly = Path(directory, "readonly")
            readonly.mkdir(mode=0o555)
            if os.geteuid() != 0:
                with self.assertRaisesRegex(RuntimeError, "not writable"):
                    REQUIRE_RESULTS_DIR(replace(EIGHT, results_dir=str(readonly)))

    def test_up_and_bootstrap_refuse_without_it_before_touching_docker(self):
        lab = replace(EIGHT, results_dir="/nonexistent/cephdag")
        real_check = mock.patch.object(lifecycle, "require_results_dir", REQUIRE_RESULTS_DIR)
        with fake_host(lab) as docker:
            with real_check, self.assertRaisesRegex(RuntimeError, "/nonexistent/cephdag"):
                lifecycle.up(lab)
            self.assertEqual(docker.trace, [])
            lifecycle.up(lab)
            before = len(docker.trace)
            with real_check, self.assertRaisesRegex(RuntimeError, "/nonexistent/cephdag"):
                lifecycle.bootstrap(lab)
            self.assertEqual(len(docker.trace), before)


class IsolationTests(unittest.TestCase):
    def test_compose_gives_each_tier_its_share_and_only_storage_nodes_a_volume(self):
        config = compose(EIGHT)
        storage, compute = config["services"]["n7"], config["services"]["c0"]
        results = {"type": "bind", "source": "/mnt/mass/cephdag", "target": "/results"}
        self.assertEqual((storage["cpus"], storage["mem_limit"]), (3, "6144m"))
        self.assertEqual(storage["volumes"], [lifecycle.data_volume(7), results])
        self.assertEqual((compute["cpus"], compute["mem_limit"]), (2, "3072m"))
        self.assertEqual(compute["volumes"], [results])
        self.assertEqual(compute["environment"]["NODE"], "8")
        self.assertEqual(compute["networks"], {"link8": {"ipv4_address": "172.30.152.10"}})
        self.assertNotIn("volumes", config["services"]["router"])
        self.assertEqual(set(config["volumes"]), {f"osd{n}" for n in range(8)})
        self.assertEqual(len(config["services"]["router"]["networks"]), 16)

    def test_valid_topology_and_no_host_shortcuts(self):
        config = compose(EIGHT)
        validate_compose(EIGHT, config)
        for node in ("n5", "c4"):
            for field, value in [
                ("privileged", True),
                ("ports", ["8080:8080"]),
                ("network_mode", "host"),
                ("devices", ["/dev/disk0"]),
            ]:
                with self.subTest(node=node, field=field):
                    bad = copy.deepcopy(config)
                    bad["services"][node][field] = value
                    with self.assertRaises(ValueError):
                        validate_compose(EIGHT, bad)
        results = {"type": "bind", "source": "/mnt/mass/cephdag", "target": "/results"}
        for node, volumes in [
            ("n0", ["/Users:/host"]),
            ("n0", [results]),
            ("n0", [lifecycle.data_volume(0), {**results, "source": "/"}]),
            ("c4", [lifecycle.data_volume(12), results]),
            ("c4", [{**results, "target": "/var/lib/ceph"}]),
            ("router", [results]),
        ]:
            with self.subTest(node=node, volumes=volumes), self.assertRaises(ValueError):
                bad = copy.deepcopy(config)
                bad["services"][node]["volumes"] = volumes
                validate_compose(EIGHT, bad)
        bad = copy.deepcopy(config)
        bad["services"]["c4"]["networks"]["link13"] = {}
        with self.assertRaises(ValueError):
            validate_compose(EIGHT, bad)
        with self.assertRaises(ValueError):
            validate_compose(EIGHT_REPLICATED, config)

    def test_refuses_collision_without_touching_existing_cluster(self):
        require_fresh(EXISTING, [], [], [])
        with self.assertRaisesRegex(RuntimeError, "existing"):
            require_fresh(EXISTING, ["cephdag-n0"], [], [])
        with self.assertRaises(RuntimeError):
            require_fresh(EXISTING, [], ["cephdag_osd2"], [])
        with self.assertRaises(RuntimeError):
            require_fresh(EXISTING, [], [], ["cephdag_link1"])
        with self.assertRaises(RuntimeError):
            require_fresh(EIGHT, ["cephdag8-c7"], [], [])
        require_fresh(EIGHT, EXISTING.containers, EXISTING.volumes, EXISTING.networks)

    def test_up_refuses_existing_names_and_overlapping_subnets(self):
        with fake_host(EXISTING, **EXISTING_RESOURCES) as docker:
            with self.assertRaisesRegex(RuntimeError, "existing"):
                lifecycle.up(EXISTING)
        self.assertEqual(docker.composes, [])
        with fake_host(EIGHT, networks=[("other", "172.30.159.0/24")]) as docker:
            with self.assertRaisesRegex(RuntimeError, "overlaps existing Docker network other"):
                lifecycle.up(EIGHT)
        self.assertEqual(docker.composes, [])

    def test_approval_is_per_lab(self):
        with fake_host(EIGHT) as docker:
            with self.assertRaisesRegex(RuntimeError, "permission"):
                lifecycle.up(EIGHT_REPLICATED)
            lifecycle.up(EIGHT)
        self.assertEqual(len(docker.composes), 1)

    def test_new_lab_never_touches_the_existing_lab(self):
        with fake_host(EIGHT, **EXISTING_RESOURCES) as docker:
            create(EIGHT)
            lifecycle.verify(EIGHT, "disagg", 100)
            lifecycle.switch_mode(EIGHT, "cs", [])
            lifecycle.verify(EIGHT, "cs", 100)
            lifecycle.switch_mode(EIGHT, "disagg", [])
            lifecycle.stop_workers(EIGHT)
            lifecycle.stop(EIGHT)
            lifecycle.resume(EIGHT)
            lifecycle.shape(EIGHT, 400.0)
        arguments = [arg for entry in docker.trace for arg in entry["args"]]
        self.assertFalse([arg for arg in arguments if re.match(r"cephdag[-_]", arg)])
        self.assertFalse([arg for arg in arguments if re.search(r"172\.30\.8[0-2]\.", arg)])
        self.assertIn(
            ["docker", "stop", *EIGHT.containers], [entry["args"] for entry in docker.trace]
        )


class BootstrapTests(unittest.TestCase):
    def test_storage_nodes_get_osds_and_pools_and_compute_nodes_only_a_client(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
        storage_conf = docker.files["cephdag8-n4", "/etc/ceph/ceph.conf"]
        self.assertIn("mon_host = [v2:172.30.144.10:3300,v1:172.30.144.10:6789]\n", storage_conf)
        self.assertIn("public_addr = 172.30.148.10\ncluster_addr = 172.30.148.10\n", storage_conf)
        self.assertIn(f"bluestore_block_size = {56 * GIB}\n", storage_conf)
        self.assertIn("osd_memory_target = 2576980377\n", storage_conf)
        compute_conf = docker.files["cephdag8-c4", "/etc/ceph/ceph.conf"]
        self.assertEqual(compute_conf, storage_conf.split("\n[osd]")[0])
        for name in STORAGE[1:] + COMPUTE:
            self.assertEqual(docker.files[name, "/var/lib/ceph/admin.keyring"], "KEY\n")
        osd_work = {
            container
            for container, args in executions(docker.trace)
            if args[0] in ("find", "ceph-osd") or "ceph-osd -f" in " ".join(args)
        }
        self.assertEqual(osd_work, set(STORAGE))
        commands = [
            entry["args"][3:]
            for entry in docker.trace
            if entry["args"][2:4] == ["cephdag8-n0", "ceph"]
        ]
        self.assertIn(
            ["ceph", "osd", "crush", "add", "osd.7", "0.0546875", "root=default", "host=n7"],
            commands,
        )
        self.assertEqual(len([args for args in commands if args[1:3] == ["osd", "create"]]), 8)
        self.assertEqual(docker.rules, {f"pin{n}": [n] for n in range(8)})
        self.assertEqual(set(docker.pools), {f"node{n}" for n in range(8)})
        self.assertEqual(docker.pools["node7"], {"rule": "pin7", "size": 1, "min_size": 1})

    def test_two_replicas_keep_the_primary_on_the_pool_node(self):
        with fake_host(EIGHT_REPLICATED) as docker:
            create(EIGHT_REPLICATED)
        pins = {name: osds for name, osds in docker.rules.items() if name != "replicated_rule"}
        self.assertEqual(pins, {f"pin{n}": [n, (n + 1) % 8] for n in range(8)})
        self.assertEqual(docker.pools["node3"], {"rule": "pin3", "size": 2, "min_size": 1})
        self.assertIn(
            "osd_pool_default_size = 2\n", docker.files["cephdag8r-n0", "/etc/ceph/ceph.conf"]
        )
        self.assertIn(
            "rule pin7 {\n\tid 8\n\ttype replicated\n"
            "\tstep take n7\n\tstep choose firstn 1 type osd\n\tstep emit\n"
            "\tstep take n0\n\tstep choose firstn 1 type osd\n\tstep emit\n}\n",
            crush_rules(EIGHT_REPLICATED),
        )


class ModeTests(unittest.TestCase):
    def test_cs_runs_the_storage_workers_and_stops_the_compute_containers(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", ["examples.custom_function"])
        self.assertEqual(docker.running, set(STORAGE) | {"cephdag8-router"})
        self.assertEqual(set(docker.workers), set(STORAGE))
        self.assertEqual(
            worker_commands(docker.trace)[7],
            [
                "docker", "exec", "-d", "cephdag8-n7", "bash", "-c",
                "cd /app && exec python3 -m cephdag.worker --node 7 --cluster cephdag/clusters/cephdag8-cs.json "
                "--functions examples.custom_function >> /var/lib/ceph/worker.log 2>&1",
            ],
        )  # fmt: skip

    def test_disagg_stops_the_storage_workers_and_runs_the_compute_workers(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", [])
            lifecycle.switch_mode(EIGHT, "disagg", [])
        self.assertEqual(docker.running, set(EIGHT.containers))
        self.assertEqual(
            docker.workers,
            {
                f"cephdag8-c{n - 8}": [
                    f"python3 -m cephdag.worker --node {n} "
                    "--cluster cephdag/clusters/cephdag8-disagg.json"
                ]
                for n in range(8, 16)
            },
        )
        killed = [
            entry["args"][2] for entry in docker.trace if entry["args"][3:5] == ["pkill", "-f"]
        ]
        self.assertEqual(killed, STORAGE)

    def test_switching_is_idempotent_and_reversible(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            for mode in ("disagg", "cs"):
                lifecycle.switch_mode(EIGHT, mode, [])
                before = len(docker.trace)
                lifecycle.switch_mode(EIGHT, mode, [])
                repeated = docker.trace[before:]
                self.assertFalse(
                    [
                        entry["args"]
                        for entry in repeated
                        if entry["args"][1] in ("start", "stop") or "-d" in entry["args"]
                        or "pkill" in entry["args"]
                    ],
                    mode,
                )  # fmt: skip
            self.assertEqual(set(docker.workers), set(STORAGE))
            self.assertEqual(docker.running, set(STORAGE) | {"cephdag8-router"})

    def test_disagg2_runs_two_workers_in_each_compute_container_without_changing_its_quota(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", [])
            lifecycle.switch_mode(EIGHT, "disagg2", ["cephdag.droid"])
        self.assertEqual(docker.running, set(EIGHT.containers))
        self.assertEqual(
            docker.workers,
            {
                f"cephdag8-c{j}": [
                    f"python3 -m cephdag.worker --node {node} "
                    "--cluster cephdag/clusters/cephdag8-disagg2.json --functions cephdag.droid"
                    for node in (8 + j, 16 + j)
                ]
                for j in range(8)
            },
        )
        readiness = [
            command[3:]
            for _, command in executions(docker.trace)
            if command[:2] == ["python3", "-c"] and "/state" in command[2]
        ]
        urls = [node["worker"] for node in cluster_file(EIGHT, "disagg2")["nodes"][8:]]
        self.assertEqual(readiness[-1], urls)
        self.assertEqual({url.rsplit(":", 1)[1] for url in urls}, {"8080", "8081"})
        self.assertFalse([entry for entry in docker.trace if entry["args"][1] == "update"])

    def test_disagg2_and_disagg_switch_in_running_containers_and_repeat_as_no_ops(self):
        # Entering a mode stops each compute container's workers once and starts the mode's;
        # repeating it changes nothing; no container starts or stops between the two modes.
        steps = (("disagg2", 2, 8 * (1 + 2)), ("disagg2", 2, 0), ("disagg", 1, 8 * (1 + 1)))
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "disagg", [])
            for mode, slots, changed in steps + (("disagg", 1, 0),):
                before = len(docker.trace)
                lifecycle.switch_mode(EIGHT, mode, [])
                repeated = [entry["args"] for entry in docker.trace[before:]]
                self.assertFalse([args for args in repeated if args[1] in ("start", "stop")])
                workers = [args for args in repeated if "-d" in args or "pkill" in args]
                self.assertEqual(len(workers), changed, mode)
                cluster = f"cephdag/clusters/cephdag8-{mode}.json"
                for container in COMPUTE:
                    commands = docker.workers[container]
                    self.assertEqual(len(commands), slots)
                    self.assertTrue(all(cluster in command for command in commands))
            lifecycle.switch_mode(EIGHT, "cs", [])
            self.assertEqual(set(docker.workers), set(STORAGE))
            self.assertEqual(docker.running, set(STORAGE) | {"cephdag8-router"})

    def test_cs2_runs_two_workers_in_each_storage_container_and_stops_the_compute_ones(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "disagg", [])
            lifecycle.switch_mode(EIGHT, "cs2", ["cephdag.droid"])
        self.assertEqual(docker.running, set(STORAGE) | {"cephdag8-router"})
        self.assertEqual(
            docker.workers,
            {
                f"cephdag8-n{n}": [
                    f"python3 -m cephdag.worker --node {node} "
                    "--cluster cephdag/clusters/cephdag8-cs2.json --functions cephdag.droid"
                    for node in (n, 8 + n)
                ]
                for n in range(8)
            },
        )
        readiness = [
            command[3:]
            for _, command in executions(docker.trace)
            if command[:2] == ["python3", "-c"] and "/state" in command[2]
        ]
        self.assertEqual(
            readiness[-1], [node["worker"] for node in cluster_file(EIGHT, "cs2")["nodes"]]
        )
        self.assertFalse([entry for entry in docker.trace if entry["args"][1] == "update"])

    def test_cs2_and_cs_switch_in_running_containers_and_repeat_as_no_ops(self):
        steps = (("cs2", 2, 8 * (1 + 2)), ("cs2", 2, 0), ("cs", 1, 8 * (1 + 1)), ("cs", 1, 0))
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", [])
            for mode, slots, changed in steps:
                before = len(docker.trace)
                lifecycle.switch_mode(EIGHT, mode, [])
                repeated = [entry["args"] for entry in docker.trace[before:]]
                self.assertFalse([args for args in repeated if args[1] in ("start", "stop")])
                workers = [args for args in repeated if "-d" in args or "pkill" in args]
                self.assertEqual(len(workers), changed, mode)
                cluster = f"cephdag/clusters/cephdag8-{mode}.json"
                for container in STORAGE:
                    commands = docker.workers[container]
                    self.assertEqual(len(commands), slots)
                    self.assertTrue(all(cluster in command for command in commands))
            self.assertEqual(docker.running, set(STORAGE) | {"cephdag8-router"})

    def test_disagg_waits_until_the_restarted_entrypoint_has_set_up_the_network(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", [])
            docker.pid1 = "/bin/bash /app/entrypoint.sh"
            with (
                mock.patch.object(lifecycle.time, "sleep"),
                mock.patch.object(lifecycle.time, "monotonic", side_effect=range(0, 10**6, 60)),
            ):
                with self.assertRaisesRegex(RuntimeError, "network setup timeout"):
                    lifecycle.switch_mode(EIGHT, "disagg", [])

    def test_stop_workers_leaves_ceph_running_and_no_worker(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", [])
            lifecycle.stop_workers(EIGHT)
            self.assertEqual(
                (docker.workers, docker.running), ({}, set(STORAGE) | {"cephdag8-router"})
            )
            lifecycle.switch_mode(EIGHT, "disagg", [])
            lifecycle.stop_workers(EIGHT)
            self.assertEqual(
                (docker.workers, docker.running), ({}, set(STORAGE) | {"cephdag8-router"})
            )

    def test_refuses_a_mode_the_lab_lacks_or_a_stopped_lab(self):
        with fake_host(EXISTING) as docker:
            create(EXISTING)
            with self.assertRaisesRegex(ValueError, "only mode is cs"):
                lifecycle.switch_mode(EXISTING, "disagg", [])
        self.assertEqual(worker_commands(docker.trace), [])
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.stop(EIGHT)
            with self.assertRaisesRegex(RuntimeError, "resume the lab first"):
                lifecycle.switch_mode(EIGHT, "cs", [])


class ShapingTests(unittest.TestCase):
    def test_shapes_every_running_node_and_every_router_port(self):
        router_ports = {("cephdag8-router", f"eth{n}") for n in range(16)}
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.shape(EIGHT, 250)
            self.assertEqual(
                set(tbf_targets(docker.trace, 250)),
                {(name, "eth0") for name in STORAGE + COMPUTE} | router_ports,
            )
            lifecycle.switch_mode(EIGHT, "cs", [])
            before = len(docker.trace)
            lifecycle.shape(EIGHT, 300)
            self.assertEqual(
                set(tbf_targets(docker.trace[before:], 300)),
                {(name, "eth0") for name in STORAGE} | router_ports,
            )

    def test_restarted_containers_get_the_rate_last_shaped(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", [])
            lifecycle.shape(EIGHT, 300)
            before = len(docker.trace)
            lifecycle.switch_mode(EIGHT, "disagg", [])
            self.assertEqual(
                tbf_targets(docker.trace[before:], 300), [(name, "eth0") for name in COMPUTE]
            )
            lifecycle.stop(EIGHT)
            before = len(docker.trace)
            lifecycle.resume(EIGHT)
            self.assertEqual(len(tbf_targets(docker.trace[before:], 300)), 16 + 16)


class VerifyTests(unittest.TestCase):
    def test_cs_checks_the_storage_nodes(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", [])
            lifecycle.verify(EIGHT, "cs", 100)
            evidence = json.loads((EIGHT.results / "validated-cs.json").read_text())
        self.assertEqual((evidence["mode"], evidence["nodes"]), ("cs", list(range(8))))
        self.assertEqual(evidence["aggregate_mbit"], {"shared_tx": 100, "shared_rx": 100})
        self.assertEqual(list(evidence["smoke"]), ["0"])
        iperf_clients = [entry for entry in docker.trace if entry["args"][3:5] == ["iperf3", "-c"]]
        self.assertEqual(len(iperf_clients), 8 * 4)
        servers = [x["args"] for x in docker.trace if x["args"][4:6] == ["iperf3", "-s"]]
        self.assertEqual(len(servers), 8 * 2)
        # Detached `docker exec` processes, not `iperf3 -D`, whose zombies PID 1 never reaps.
        self.assertTrue(all(args[2] == "-d" and "-D" not in args for args in servers))

    def test_disagg_needs_the_compute_containers(self):
        with fake_host(EIGHT):
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs", [])
            with self.assertRaisesRegex(RuntimeError, "cephdag8-c7 stopped; run `mode disagg`"):
                lifecycle.verify(EIGHT, "disagg", 100)

    def test_disagg_checks_every_nic_and_rados_from_every_compute_node(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "disagg", [])
            lifecycle.verify(EIGHT, "disagg", 100)
            evidence = json.loads((EIGHT.results / "validated-disagg.json").read_text())
        self.assertEqual(evidence["nodes"], list(range(16)))
        self.assertEqual(len(evidence["per_node_mbit"]), 16)
        self.assertEqual(list(evidence["smoke"]), [str(n) for n in [0, *range(8, 16)]])
        self.assertTrue(all(len(rows) == 8 for rows in evidence["smoke"].values()))
        routes = {
            entry["args"][2]
            for entry in docker.trace
            if entry["args"][3:7] == ["ip", "-j", "route", "get"]
        }
        self.assertEqual(routes, set(STORAGE + COMPUTE))
        iperf_clients = [entry for entry in docker.trace if entry["args"][3:5] == ["iperf3", "-c"]]
        self.assertEqual(len(iperf_clients), 16 * 4)

    def test_disagg2_checks_the_same_nodes_as_disagg(self):
        with fake_host(EIGHT):
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "disagg2", [])
            lifecycle.verify(EIGHT, "disagg2", 100)
            evidence = json.loads((EIGHT.results / "validated-disagg2.json").read_text())
        self.assertEqual(evidence["nodes"], list(range(16)))
        self.assertEqual(list(evidence["smoke"]), [str(n) for n in [0, *range(8, 16)]])

    def test_cs2_checks_the_same_nodes_as_cs(self):
        with fake_host(EIGHT):
            create(EIGHT)
            lifecycle.switch_mode(EIGHT, "cs2", [])
            lifecycle.verify(EIGHT, "cs2", 100)
            lifecycle.shape(EIGHT, 100)
            lifecycle.check(EIGHT, "cs2", 100)
            evidence = json.loads((EIGHT.results / "validated-cs2.json").read_text())
            checked = json.loads((EIGHT.results / "checked-cs2.json").read_text())
        self.assertEqual(evidence["nodes"], list(range(8)))
        self.assertEqual(list(evidence["smoke"]), ["0"])
        self.assertEqual(checked["nodes"], list(range(8)))

    def test_measures_each_flow_at_its_sender(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            docker.late_receivers.add(("cephdag8-n1", EIGHT.address(3)))
            lifecycle.verify(EIGHT, "cs", 100)
            evidence = json.loads((EIGHT.results / "validated-cs.json").read_text())
        self.assertEqual(evidence["aggregate_mbit"], {"shared_tx": 100.0, "shared_rx": 100.0})

    def test_rejects_a_single_slow_compute_boundary(self):
        with fake_host(EIGHT) as docker:
            create(EIGHT)
            docker.flow_mbit["cephdag8-c7", EIGHT.address(14)] = 10
            with self.assertRaisesRegex(RuntimeError, r"failed at nodes \[14\]"):
                lifecycle.verify(EIGHT, "disagg", 100)
            self.assertFalse((EIGHT.results / "validated-disagg.json").exists())

    def test_rejects_a_replica_set_whose_primary_is_elsewhere(self):
        with fake_host(EIGHT_REPLICATED) as docker:
            create(EIGHT_REPLICATED)
            docker.placement_override["node3"] = {
                "up": [4, 3],
                "up_primary": 4,
                "acting": [4, 3],
                "acting_primary": 4,
            }
            with self.assertRaisesRegex(RuntimeError, r"placement is not \[3, 4\]"):
                lifecycle.verify(EIGHT_REPLICATED, "cs", 100)

    def test_placement_must_match_exactly_with_the_pool_node_as_primary(self):
        validate_placement([1], {"up": [1], "up_primary": 1, "acting": [1], "acting_primary": 1})
        validate_placement(
            [1, 2], {"up": [1, 2], "up_primary": 1, "acting": [1, 2], "acting_primary": 1}
        )
        for osds, value in [
            ([1], {"up": [0], "up_primary": 0, "acting": [0], "acting_primary": 0}),
            ([1], {"up": [1, 2], "up_primary": 1, "acting": [1, 2], "acting_primary": 1}),
            ([1], {"up": [1], "acting": []}),
            ([1], {"up": [1], "acting": [1]}),
            ([1, 2], {"up": [1], "up_primary": 1, "acting": [1], "acting_primary": 1}),
            ([1, 2], {"up": [1, 2], "up_primary": 1, "acting": [2, 1], "acting_primary": 2}),
        ]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                validate_placement(osds, value)

    def test_pg_readiness_handles_ceph20_schema_and_rejects_empty(self):
        self.assertTrue(
            pgs_clean(
                {
                    "pg_ready": True,
                    "pg_summary": {
                        "num_pgs": 97,
                        "num_pg_by_state": [{"name": "active+clean", "num": 97}],
                    },
                }
            )
        )
        self.assertFalse(pgs_clean({"pg_summary": {"num_pgs": 0, "num_pg_by_state": []}}))
        self.assertFalse(
            pgs_clean(
                {"pg_summary": {"num_pgs": 2, "num_pg_by_state": [{"name": "peering", "num": 2}]}}
            )
        )


class CheckTests(unittest.TestCase):
    def switched(self, docker, mode):
        create(EIGHT)
        lifecycle.shape(EIGHT, 400)
        lifecycle.switch_mode(EIGHT, mode, [])
        return len(docker.trace)

    def test_passes_a_shaped_healthy_lab_without_measuring_bandwidth(self):
        for mode, nodes in (
            ("cs", STORAGE),
            ("disagg", STORAGE + COMPUTE),
            ("disagg2", STORAGE + COMPUTE),
        ):
            with self.subTest(mode=mode), fake_host(EIGHT) as docker:
                before = self.switched(docker, mode)
                lifecycle.check(EIGHT, mode, 400)
                evidence = json.loads((EIGHT.results / f"checked-{mode}.json").read_text())
                shown = {
                    (container, command[5])
                    for container, command in executions(docker.trace[before:])
                    if command[:4] == ["tc", "-j", "qdisc", "show"]
                }
            router_ports = {("cephdag8-router", f"eth{n}") for n in range(16)}
            self.assertEqual(shown, {(name, "eth0") for name in nodes} | router_ports)
            self.assertEqual(evidence["nodes"], list(range(len(nodes))))
            self.assertEqual(evidence["health"], "HEALTH_WARN")
            commands = [command for _, command in executions(docker.trace[before:])]
            self.assertFalse(any("iperf3" in command for command in commands))

    def test_refuses_an_unshaped_link_or_one_at_another_rate(self):
        with fake_host(EIGHT) as docker:
            self.switched(docker, "cs")
            del docker.qdiscs["cephdag8-n3", "eth0"]
            with self.assertRaisesRegex(RuntimeError, "at 400 Mbit/s: cephdag8-n3:eth0$"):
                lifecycle.check(EIGHT, "cs", 400)
            with self.assertRaisesRegex(RuntimeError, "cephdag8-router:eth15"):
                lifecycle.check(EIGHT, "cs", 300)
        with fake_host(EIGHT) as docker:
            self.switched(docker, "disagg")
            docker.qdiscs["cephdag8-c5", "eth0"] = 10000
            with self.assertRaisesRegex(RuntimeError, "cephdag8-c5:eth0"):
                lifecycle.check(EIGHT, "disagg", 400)

    def test_refuses_missing_workers_ceph_errors_and_unclean_placement_groups(self):
        with fake_host(EIGHT) as docker:
            self.switched(docker, "cs")
            worker = docker.workers.pop("cephdag8-n6")
            with self.assertRaisesRegex(RuntimeError, "no worker answers"):
                lifecycle.check(EIGHT, "cs", 400)
            docker.workers["cephdag8-n6"] = worker
            docker.health = "HEALTH_ERR"
            with self.assertRaisesRegex(RuntimeError, "HEALTH_ERR"):
                lifecycle.check(EIGHT, "cs", 400)
            docker.health = "HEALTH_WARN"
            docker.pg_states = ["active+clean", "active+undersized+degraded"]
            with self.assertRaisesRegex(RuntimeError, "active\\+clean"):
                lifecycle.check(EIGHT, "cs", 400)
            docker.pg_states = ["active+clean"]
            lifecycle.check(EIGHT, "cs", 400)
            with self.assertRaisesRegex(RuntimeError, "cephdag8-c0 .*stopped; run `mode disagg`"):
                lifecycle.check(EIGHT, "disagg", 400)


class ImageLockTests(unittest.TestCase):
    def test_droid_lock_adds_one_hashed_wheel_per_package_to_the_main_lock(self):
        def pins(name):
            lines = Path("infra", name).read_text().splitlines()
            return [re.fullmatch(r"(\S+)==\S+ --hash=sha256:[0-9a-f]{64}", line) for line in lines]

        main = pins("requirements-linux-amd64.lock")
        droid = pins("requirements-droid-linux-amd64.lock")
        self.assertTrue(all(main) and all(droid))
        packages = [pin.group(1) for pin in droid]
        self.assertEqual(packages, ["av", "pillow", "pyarrow"])
        self.assertFalse(set(packages) & {pin.group(1) for pin in main})

    def test_image_holds_the_native_library_where_the_policies_load_it(self):
        dockerfile = Path("infra/Dockerfile").read_text()
        library = native.LIBRARY.relative_to(Path(native.__file__).resolve().parents[2])
        copied = re.search(r"^COPY --from=native \S+ (\S+)$", dockerfile, re.MULTILINE)
        self.assertEqual(copied.group(1), f"/app/{library}/libcephdag_native.so")

    def test_rust_toolchain_is_one_pinned_archive_per_component(self):
        lines = Path("infra/rust-toolchain.sha256").read_text().splitlines()
        archive = r"[0-9a-f]{64}  (\S+)-1\.85\.0-x86_64-unknown-linux-gnu\.tar\.xz"
        names = [re.fullmatch(archive, line) for line in lines]
        self.assertEqual([name.group(1) for name in names], ["rustc", "rust-std", "cargo"])


class NetworkTests(unittest.TestCase):
    def test_sixteen_nodes_route_through_their_own_tunnel(self):
        commands = network_commands(network, compose(EIGHT))
        tunnels = [args for args in commands["router"] if args[:3] == ["ip", "tunnel", "add"]]
        self.assertEqual(len(tunnels), 16)
        self.assertIn(
            "ip tunnel add lab15 mode ipip local 172.30.159.254 remote 172.30.159.10 dev eth0".split(),
            tunnels,
        )
        node = commands["c4"]
        routes = [args for args in node if args[:3] == ["ip", "route", "replace"]]
        self.assertEqual(len(routes), 15)
        self.assertNotIn(str(EIGHT.subnet(12)), [args[3] for args in routes])
        self.assertTrue(
            all(args[5] == "10.77.12.2" and args[9] == "172.30.156.10" for args in routes)
        )
        self.assertEqual(node[-1][-5], "400mbit")


if __name__ == "__main__":
    unittest.main()
