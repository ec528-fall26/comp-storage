"""The host-side campaign driver against a fake lab: order, resume and fail-closed."""

import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from cephdag.graph import Dataset, ObjectRef
from cephdag.policies import make_policy
from experiments import campaign, droid_ingest, run

SPECS = Path(campaign.__file__).parent / "campaigns"
SYNTHETIC = json.loads((SPECS / "demo3-scale.json").read_text())
DROID = json.loads((SPECS / "demo3-droid.json").read_text())


class FakeLab:
    """Records every command; experiments.run writes the rows a real run would and takes
    `run_seconds` of the lab's clock."""

    def __init__(self, spec, fail=None, digests=None, passed=True, run_seconds=60, finals=None):
        self.spec = spec
        self.commands = []
        self.fail = fail or (lambda words: False)
        self.digests = digests or {}
        self.finals = finals or {}
        self.passed = passed
        self.run_seconds = run_seconds
        self.now = 0.0

    def __call__(self, argv, log):
        self.commands.append(argv)
        words = " ".join(argv)
        if self.fail(words):
            return 1, f"failed: {words}"
        if "experiments.run" in argv:
            self.write_rows(argv)
            self.now += self.run_seconds
        return 0, ""

    def clock(self):
        return self.now

    def write_rows(self, argv):
        value = dict(zip(argv, argv[1:]))
        mode = Path(value["--cluster"]).stem.rsplit("-", 1)[1]
        output = campaign.host_path(self.spec, value["--output"])
        output.mkdir(parents=True)
        variant = "fused" if "--droid-fused" in argv else "unfused"
        digest = self.digests.get(mode, variant)
        with open(output / "rows.jsonl", "w") as rows:
            for policy in value["--policies"].split(","):
                verification = {"passed": self.passed, "outputs_sha256": digest}
                if value["--workload"] == "droid":
                    verification["final_outputs_sha256"] = self.finals.get(variant, "finals")
                row = {"mode": mode, "policy": policy, "verification": verification}
                rows.write(json.dumps(row) + "\n")

    def lab_actions(self):
        return [argv[4:] for argv in self.commands if argv[1] == "infra/lab.py"]

    def runs(self):
        return [argv for argv in self.commands if "experiments.run" in argv]


def small_synthetic(results):
    spec = copy.deepcopy(SYNTHETIC)
    spec["results_host"] = str(results)
    spec["synthetic"].update(repeats=2, intensities=[1, 8])
    spec["synthetic"]["workloads"] = spec["synthetic"]["workloads"][:1]
    return spec


def small_droid(results):
    spec = copy.deepcopy(DROID)
    spec["results_host"] = str(results)
    spec["droid"].update(shards="10-19", wave_shards=4)
    return spec


def provide_inputs(spec):
    """The calibrated models and raw shards preflight looks for."""
    for settings in spec["modes"].values():
        models = campaign.host_path(spec, settings["models"])
        models.parent.mkdir(parents=True, exist_ok=True)
        functions = {name: {} for name in ("a", "b", "c", "d", "droid_fused@1")}
        models.write_text(json.dumps({"compute": {"functions": functions}}))
    if spec["kind"] == "droid":
        raw = campaign.host_path(spec, spec["droid"]["source_dir"])
        raw.mkdir(parents=True)
        for shard in campaign.parse_range(spec["droid"]["shards"]):
            (raw / campaign.droid_shard_name(shard)).write_bytes(b"\0" * 1000)


class CampaignTestCase(unittest.TestCase):
    def setUp(self):
        self.results = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.results)

    def drive(self, spec, lab, acknowledge=False):
        driver = campaign.Campaign(spec, runner=lab, clock=lab.clock, say=lambda *args: None)
        driver.run(acknowledge)
        return driver

    def events(self, spec):
        return campaign.Ledger(campaign.campaign_directory(spec) / "ledger.jsonl").events()


class OrderTests(CampaignTestCase):
    def test_each_repeat_runs_its_cases_in_one_mode_then_the_other_alternating_which_first(self):
        spec = small_synthetic(self.results)
        steps = campaign.steps_of(spec)
        self.assertEqual(
            [step["id"] for step in steps],
            [
                "create",
                *("independent-i1-r0-cs", "independent-i8-r0-cs"),
                *("independent-i1-r0-disagg", "independent-i8-r0-disagg"),
                *("independent-i1-r1-disagg", "independent-i8-r1-disagg"),
                *("independent-i1-r1-cs", "independent-i8-r1-cs"),
                "delete",
            ],
        )
        seeds = {step["id"]: step["seed"] for step in steps if step["kind"] == "run"}
        for case, seed in (("i1-r0", 9103), ("i8-r0", 9104), ("i1-r1", 9105), ("i8-r1", 9106)):
            for mode in ("cs", "disagg"):
                self.assertEqual(seeds[f"independent-{case}-{mode}"], seed)

    def test_droid_waves_ingest_run_both_modes_and_delete_in_turn(self):
        steps = campaign.steps_of(small_droid(self.results))
        self.assertEqual(
            [step["id"] for step in steps],
            [
                *("wave00-ingest", "wave00-cs", "wave00-disagg", "wave00-delete"),
                *("wave01-ingest", "wave01-disagg", "wave01-cs", "wave01-delete"),
                *("wave02-ingest", "wave02-cs", "wave02-disagg", "wave02-delete"),
            ],
        )
        self.assertEqual(
            [step["shards"] for step in steps if step["kind"] == "bulk"],
            [[10, 13], [14, 17], [18, 19]],
        )

    def test_three_modes_run_in_the_spec_order_then_reversed_each_with_its_files(self):
        spec = small_synthetic(self.results)
        spec["synthetic"]["intensities"] = [1]
        spec["modes"]["disagg2"] = {
            "cluster": "cephdag/clusters/cephdag8-disagg2.json",
            "models": "/results/calibration/sensitivity/cephdag8-disagg2.json",
            "policies": ["fifo-hash"],
        }
        steps = campaign.steps_of(spec)
        self.assertEqual(
            [step["id"] for step in steps if step["kind"] == "run"],
            [
                *("independent-i1-r0-cs", "independent-i1-r0-disagg", "independent-i1-r0-disagg2"),
                *("independent-i1-r1-disagg2", "independent-i1-r1-disagg", "independent-i1-r1-cs"),
            ],
        )
        provide_inputs(spec)
        lab = FakeLab(spec)
        self.drive(spec, lab)
        modes = [action[1] for action in lab.lab_actions() if action[0] == "mode"]
        self.assertEqual(modes, ["cs", "disagg", "disagg2", "disagg", "cs"])
        last = dict(zip(lab.runs()[3], lab.runs()[3][1:]))
        self.assertEqual(last["--cluster"], "cephdag/clusters/cephdag8-disagg2.json")
        self.assertEqual(last["--models"], spec["modes"]["disagg2"]["models"])
        self.assertEqual(last["--policies"], "fifo-hash")
        missing = small_synthetic(self.results / "missing")
        missing["modes"]["disagg2"] = dict(spec["modes"]["disagg2"], cluster="no/such.json")
        self.assertIn("disagg2: cluster file no/such.json is missing", campaign.preflight(missing))

    def test_droid_variants_run_every_mode_unfused_then_fused_and_must_agree(self):
        spec = small_droid(self.results)
        spec["droid"].update(shards="10-13", variants=["unfused", "fused"])
        steps = campaign.steps_of(spec)
        self.assertEqual(
            [step["id"] for step in steps],
            [
                *("wave00-ingest", "wave00-cs", "wave00-disagg"),
                *("wave00-fused-cs", "wave00-fused-disagg", "wave00-delete"),
            ],
        )
        rates = spec["estimate"]
        videos = rates["frames_per_source"] * rates["video_per_frames"]
        self.assertEqual(campaign.droid_factors(rates, fused=True), (1, videos, videos))
        seconds = campaign.estimate(spec)[0]
        self.assertLess(seconds["wave00-fused-disagg"], seconds["wave00-disagg"])
        provide_inputs(spec)
        lab = FakeLab(spec)
        self.drive(spec, lab)
        fused = ["--droid-fused" in argv for argv in lab.runs()]
        self.assertEqual(fused, [False, False, True, True])
        spec = small_droid(self.results / "differ")
        spec["droid"].update(shards="10-13", variants=["unfused", "fused"])
        provide_inputs(spec)
        lab = FakeLab(spec, finals={"fused": "other"})
        with self.assertRaisesRegex(
            campaign.CampaignStopped, "wave00-fused-cs videos, statistics and Parquet other"
        ):
            self.drive(spec, lab)
        spec["modes"]["cs"]["models"] = "/results/calibration/unfused-only.json"
        models = campaign.host_path(spec, spec["modes"]["cs"]["models"])
        models.write_text(json.dumps({"compute": {"functions": dict.fromkeys("abcd", {})}}))
        self.assertIn("have no droid_fused@1 cost", campaign.preflight(spec)[0])

    def test_each_step_prints_when_it_starts_and_ends_with_the_time(self):
        spec = small_synthetic(self.results)
        provide_inputs(spec)
        lines = []
        lab = FakeLab(spec)
        driver = campaign.Campaign(spec, runner=lab, clock=lab.clock, say=lines.append)
        driver.run()
        steps = [step["id"] for step in campaign.steps_of(spec)]
        expected = [f"{spec['name']}: 0 of {len(steps)} done"]
        for step in steps:
            expected += [f"{step}: attempt 1 started", f"{step}: attempt 1 completed in 0 s"]
        self.assertEqual(lines, expected + [f"{spec['name']}: complete"])

        class Stdout(io.StringIO):
            flushes = 0

            def flush(self):
                Stdout.flushes += 1

        with contextlib.redirect_stdout(Stdout()) as out:
            campaign.progress("create: attempt 1 started")
        self.assertRegex(
            out.getvalue(), r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \S* ?create: attempt 1 started\n$"
        )
        self.assertEqual(Stdout.flushes, 1)

    def test_a_session_verifies_its_first_mode_then_checks_each_switch_lightly(self):
        spec = small_synthetic(self.results)
        provide_inputs(spec)
        lab = FakeLab(spec)
        self.drive(spec, lab)
        self.assertEqual(
            lab.lab_actions(),
            [
                ["stop-workers"],
                ["shape", "400"],
                *(["shape", "10000"], ["shape", "400"]),
                *(["mode", "cs"], ["shape", "400"], ["verify", "cs"]),
                *(["mode", "disagg"], ["shape", "400"], ["check", "disagg"]),
                *(["mode", "cs"], ["shape", "400"], ["check", "cs"]),
            ],
        )
        create = lab.commands[3]
        self.assertEqual(create[:5], ["docker", "exec", "cephdag8-n0", "python3", "-m"])
        self.assertEqual(create[5:7], ["experiments.datasets", "create-synthetic"])
        first = dict(zip(lab.runs()[0], lab.runs()[0][1:]))
        self.assertEqual(first["--cluster"], "cephdag/clusters/cephdag8-cs.json")
        self.assertEqual(first["--policies"], ",".join(spec["modes"]["cs"]["policies"]))
        self.assertEqual(
            first["--output"], "/results/campaigns/demo3-scale/independent-i1-r0-cs/a1"
        )
        self.assertEqual(
            lab.commands[-1][5:8], ["experiments.datasets", "delete", "demo3-synthetic-256g-64m"]
        )
        completed = [event for event in self.events(spec) if event["event"] == "completed"]
        self.assertEqual(len(completed), 10)
        again = FakeLab(spec)
        self.drive(spec, again)
        self.assertEqual(again.commands, [])

    def test_a_full_verify_runs_again_once_six_hours_have_passed_since_the_last(self):
        spec = small_synthetic(self.results)
        provide_inputs(spec)
        lab = FakeLab(spec, run_seconds=2.5 * 3600)
        self.drive(spec, lab)
        checks = [action for action in lab.lab_actions() if action[0] in ("verify", "check")]
        # Switches at 0 h, 5 h (cs to disagg after two runs) and 15 h (back to cs).
        self.assertEqual(checks, [["verify", "cs"], ["check", "disagg"], ["verify", "cs"]])

    def test_without_verify_every_switch_is_checked_lightly(self):
        spec = small_synthetic(self.results)
        spec["verify"] = False
        provide_inputs(spec)
        lab = FakeLab(spec)
        self.drive(spec, lab)
        checks = [action for action in lab.lab_actions() if action[0] in ("verify", "check")]
        self.assertEqual(checks, [["check", "cs"], ["check", "disagg"], ["check", "cs"]])

    def test_droid_workers_load_the_droid_functions(self):
        spec = small_droid(self.results)
        provide_inputs(spec)
        lab = FakeLab(spec)
        self.drive(spec, lab)
        modes = [action for action in lab.lab_actions() if action[0] == "mode"]
        self.assertEqual(modes[0], ["mode", "cs", "--functions", "cephdag.droid"])
        ingest = [argv for argv in lab.commands if "experiments.droid_ingest" in argv]
        self.assertEqual(len(ingest), 3)
        self.assertIn("/results/droid/raw", ingest[0])
        deletes = [argv for argv in lab.commands if "delete" in argv]
        self.assertEqual(
            [argv[argv.index("--droid-shards") + 1] for argv in deletes],
            ["10-13", "14-17", "18-19"],
        )

    def test_a_campaign_without_cs_creates_and_deletes_its_data_with_its_first_cluster(self):
        spec = json.loads((SPECS / "demo3-slots.json").read_text())
        spec["results_host"] = str(self.results)
        spec["synthetic"]["intensities"] = [1]
        steps = campaign.steps_of(spec)
        for step in (steps[0], steps[-1]):
            arguments = dict(zip(step["arguments"], step["arguments"][1:]))
            self.assertEqual(arguments["--cluster"], "cephdag/clusters/cephdag8-cs2.json")
        provide_inputs(spec)
        lab = FakeLab(spec)
        self.drive(spec, lab)
        modes = [action[1] for action in lab.lab_actions() if action[0] == "mode"]
        self.assertEqual(modes, ["cs2", "disagg2", "cs2"])
        cs2 = next(step for step in steps if step["id"] == "independent-i1-r0-cs2")
        local = campaign.synthetic_run_seconds(spec, cs2)
        remote = campaign.synthetic_run_seconds(spec, dict(cs2, mode="disagg2"))
        self.assertLess(local, remote)


class ResumeTests(CampaignTestCase):
    def test_a_failed_step_stops_the_campaign_until_acknowledged_and_completed_steps_never_rerun(
        self,
    ):
        spec = small_synthetic(self.results)
        provide_inputs(spec)
        failing = FakeLab(spec, fail=lambda words: "independent-i8-r0-cs/a1" in words)
        with self.assertRaisesRegex(campaign.CampaignStopped, "independent-i8-r0-cs.*exited 1"):
            self.drive(spec, failing)
        self.assertEqual(len(failing.runs()), 2)
        with self.assertRaisesRegex(campaign.CampaignStopped, "acknowledge-failure"):
            self.drive(spec, FakeLab(spec))
        resumed = FakeLab(spec)
        self.drive(spec, resumed, acknowledge=True)
        outputs = [dict(zip(argv, argv[1:]))["--output"] for argv in resumed.runs()]
        self.assertEqual(
            [output.split("/")[-2:] for output in outputs],
            [
                ["independent-i8-r0-cs", "a2"],
                ["independent-i1-r0-disagg", "a1"],
                ["independent-i8-r0-disagg", "a1"],
                ["independent-i1-r1-disagg", "a1"],
                ["independent-i8-r1-disagg", "a1"],
                ["independent-i1-r1-cs", "a1"],
                ["independent-i8-r1-cs", "a1"],
            ],
        )
        self.assertNotIn(["shape", "10000"], resumed.lab_actions())
        # A new session verifies its first mode in full, whatever the last session did.
        self.assertEqual(
            resumed.lab_actions()[:5],
            [
                ["stop-workers"],
                ["shape", "400"],
                ["mode", "cs"],
                ["shape", "400"],
                ["verify", "cs"],
            ],
        )
        failed = [event for event in self.events(spec) if event["event"] == "failed"]
        self.assertEqual([event["step"] for event in failed], ["independent-i8-r0-cs"])

    def test_a_step_interrupted_without_a_record_runs_again_as_a_new_attempt(self):
        spec = small_synthetic(self.results)
        provide_inputs(spec)
        ledger = campaign.Ledger(campaign.campaign_directory(spec) / "ledger.jsonl")
        ledger.path.parent.mkdir(parents=True)
        ledger.append(event="started", step="create", attempt=1, output="x")
        lab = FakeLab(spec)
        self.drive(spec, lab)
        self.assertEqual(len([argv for argv in lab.commands if "create-synthetic" in argv]), 1)
        started = [event for event in self.events(spec) if event["step"] == "create"]
        self.assertEqual([event["attempt"] for event in started], [1, 2, 2])

    def test_outputs_that_differ_between_modes_stop_the_campaign(self):
        spec = small_synthetic(self.results)
        provide_inputs(spec)
        lab = FakeLab(spec, digests={"cs": "one", "disagg": "two"})
        with self.assertRaisesRegex(
            campaign.CampaignStopped, "independent-i1-r0-disagg outputs two differ"
        ):
            self.drive(spec, lab)
        self.assertEqual(len(lab.runs()), 3)

    def test_a_failed_verification_stops_the_campaign(self):
        spec = small_synthetic(self.results)
        provide_inputs(spec)
        with self.assertRaisesRegex(campaign.CampaignStopped, "verification failed"):
            self.drive(spec, FakeLab(spec, passed=False))

    def test_a_lab_error_stops_the_campaign_and_bulk_steps_restore_the_shaping(self):
        spec = small_droid(self.results)
        provide_inputs(spec)
        lab = FakeLab(spec, fail=lambda words: "droid_ingest" in words)
        with self.assertRaisesRegex(campaign.CampaignStopped, "wave00-ingest"):
            self.drive(spec, lab)
        self.assertEqual(lab.lab_actions()[-2:], [["shape", "10000"], ["shape", "400"]])
        for failing, runs_before in (("verify cs", 0), ("check disagg", 2)):
            with self.subTest(failing=failing):
                spec = small_synthetic(self.results / failing.replace(" ", "-"))
                provide_inputs(spec)
                lab = FakeLab(spec, fail=lambda words, end=failing: words.endswith(end))
                with self.assertRaisesRegex(campaign.CampaignStopped, f"lab {failing} exited 1"):
                    self.drive(spec, lab)
                self.assertEqual(len(lab.runs()), runs_before)

    def test_preflight_refuses_missing_models_raw_shards_and_overfull_nodes(self):
        spec = small_droid(self.results)
        lab = FakeLab(spec)
        with self.assertRaisesRegex(campaign.CampaignStopped, "models .* are missing"):
            self.drive(spec, lab)
        self.assertEqual(lab.commands, [])
        provide_inputs(spec)
        (campaign.host_path(spec, "/results/droid/raw") / campaign.droid_shard_name(12)).unlink()
        self.assertEqual(
            campaign.preflight(spec),
            ["1 raw shards missing in " + str(self.results / "droid/raw") + ", first 12"],
        )
        spec = small_synthetic(self.results)
        spec["synthetic"]["resident_gib"] = 380
        self.assertIn("above 47.6 GiB", campaign.preflight(spec)[-1])


class EstimateTests(unittest.TestCase):
    def test_profiles_match_the_workload_graphs(self):
        shards = tuple(ObjectRef(f"node{i % 2}", f"s/{i}", 2**20, partition=i) for i in range(4))
        dataset = Dataset("d", shards)
        for workload in ("independent", "competing"):
            graph = run.build_workload(dataset, workload, 3, 0.25)
            sizes = {}
            read = written = iteration = 0
            for task in graph.tasks.values():
                inputs = [
                    ref.size if isinstance(ref, ObjectRef) else sizes[ref.task][ref.output]
                    for ref in task.inputs
                ]
                sizes[task.id] = task.output_sizes
                read += sum(inputs)
                written += sum(task.output_sizes)
                iteration += max(inputs) * task.params["iterations"]
            source = 4 * 2**20
            expected = campaign.synthetic_profile(workload, 0.25, 3)
            self.assertEqual(
                (len(graph.tasks) / 4, read / source, written / source, iteration / source),
                expected,
            )

    def test_the_sensitivity_campaigns_use_new_shards_and_every_run_lists_its_cluster(self):
        sweep = json.loads((SPECS / "demo3-sensitivity-sweep.json").read_text())
        droid = json.loads((SPECS / "demo3-sensitivity-droid.json").read_text())
        main = [step["shards"] for step in campaign.steps_of(DROID) if step["kind"] == "bulk"]
        self.assertEqual(max(last for _, last in main), 549)
        self.assertEqual(
            [step["shards"] for step in campaign.steps_of(droid) if step["kind"] == "bulk"],
            [[550, 552]],
        )
        for spec in (sweep, droid):
            self.assertEqual(list(spec["modes"]), ["cs", "disagg", "disagg2"])
            self.assertEqual(spec["modes"]["disagg2"]["policies"], ["fifo-hash"])
            for settings in spec["modes"].values():
                self.assertTrue((campaign.REPO / settings["cluster"]).exists())
        runs = [step for step in campaign.steps_of(droid) if step["kind"] == "run"]
        self.assertEqual([step["fused"] for step in runs], [False] * 3 + [True] * 3)

    def test_the_overlap_sweep_repeats_the_full_sweep_with_a_two_worker_baseline(self):
        full = json.loads((SPECS / "demo3-sweep.json").read_text())
        overlap = json.loads((SPECS / "demo3-overlap-sweep.json").read_text())
        sensitivity = json.loads((SPECS / "demo3-sensitivity-sweep.json").read_text())
        for key in ("resident_gib", "shard_mib", "placement", "workloads"):
            self.assertEqual(overlap["synthetic"][key], full["synthetic"][key])
        self.assertLessEqual(
            set(overlap["synthetic"]["intensities"]), set(full["synthetic"]["intensities"])
        )
        datasets = {spec["synthetic"]["dataset"] for spec in (SYNTHETIC, full, sensitivity)}
        self.assertNotIn(overlap["synthetic"]["dataset"], datasets)
        self.assertTrue(overlap["synthetic"]["delete_at_end"])
        self.assertEqual(list(overlap["modes"]), ["cs", "disagg", "disagg2"])
        for mode in ("cs", "disagg"):
            self.assertEqual(overlap["modes"][mode]["models"], full["modes"][mode]["models"])
        self.assertEqual(overlap["modes"]["disagg2"], sensitivity["modes"]["disagg2"])
        self.assertEqual(overlap["modes"]["disagg2"]["policies"], ["fifo-hash"])
        for settings in overlap["modes"].values():
            self.assertTrue((campaign.REPO / settings["cluster"]).exists())
            for policy in settings["policies"]:
                make_policy(policy)

    def test_the_slot_matched_sweep_repeats_the_overlap_sweep_with_cs2_on_its_own_data(self):
        overlap = json.loads((SPECS / "demo3-overlap-sweep.json").read_text())
        slots = json.loads((SPECS / "demo3-slots.json").read_text())
        for key in ("resident_gib", "shard_mib", "placement", "workloads", "repeats"):
            self.assertEqual(slots["synthetic"][key], overlap["synthetic"][key])
        self.assertEqual(slots["synthetic"]["intensities"], [1, 64, 256, 1024, 4096])
        runs = [step["id"] for step in campaign.steps_of(slots) if step["kind"] == "run"]
        self.assertEqual(len(runs), 2 * (5 + 4))
        self.assertEqual(
            [run for run in runs if "-i4096-" in run],
            ["independent-i4096-r0-cs2", "independent-i4096-r0-disagg2"],
        )
        self.assertIn("independent-i1024-r1-cs2", runs)
        others = {
            json.loads(path.read_text()).get("synthetic", {}).get("dataset")
            for path in SPECS.glob("*.json")
            if path.name != "demo3-slots.json"
        }
        self.assertNotIn(slots["synthetic"]["dataset"], others)
        self.assertTrue(slots["synthetic"]["delete_at_end"])
        self.assertEqual(list(slots["modes"]), ["cs2", "disagg2"])
        self.assertEqual(slots["modes"]["cs2"]["policies"], ["data-local", "heft"])
        self.assertEqual(slots["modes"]["disagg2"], overlap["modes"]["disagg2"])
        for settings in slots["modes"].values():
            self.assertTrue((campaign.REPO / settings["cluster"]).exists())
            for policy in settings["policies"]:
                make_policy(policy)

    def test_the_slot_matched_droid_campaign_adds_cs2_on_the_sensitivity_shards(self):
        sensitivity = json.loads((SPECS / "demo3-sensitivity-droid.json").read_text())
        slots = json.loads((SPECS / "demo3-slots-droid.json").read_text())
        self.assertEqual(slots["droid"], sensitivity["droid"])
        self.assertEqual(slots["droid"]["variants"], ["unfused", "fused"])
        self.assertEqual(list(slots["modes"]), ["cs", "cs2", "disagg2"])
        for mode in ("cs", "disagg2"):
            self.assertEqual(slots["modes"][mode], sensitivity["modes"][mode])
        self.assertEqual(slots["modes"]["cs2"]["policies"], ["data-local"])
        runs = [step for step in campaign.steps_of(slots) if step["kind"] == "run"]
        self.assertEqual(
            [(step["mode"], step["fused"]) for step in runs],
            [(mode, fused) for fused in (False, True) for mode in ("cs", "cs2", "disagg2")],
        )

    def test_the_scheduler_campaigns_repeat_skew_and_uniform_on_their_own_data(self):
        skew = json.loads((SPECS / "demo3-skew.json").read_text())
        sweep = json.loads((SPECS / "demo3-sweep.json").read_text())
        others = {
            json.loads(path.read_text()).get("synthetic", {}).get("dataset")
            for path in SPECS.glob("*.json")
            if not path.name.startswith("sched-")
        }
        cases = {"sched-pilot": skew, "sched-skew": skew, "sched-uniform": sweep}
        datasets = set()
        for name, base in cases.items():
            spec = json.loads((SPECS / f"{name}.json").read_text())
            synthetic = spec["synthetic"]
            with self.subTest(spec=name):
                self.assertEqual(spec["name"], name)
                self.assertEqual(list(spec["modes"]), ["cs"])
                self.assertEqual(spec["modes"]["cs"]["models"], base["modes"]["cs"]["models"])
                for key in ("shard_mib", "placement", "resident_gib"):
                    self.assertEqual(synthetic[key], base["synthetic"][key])
                self.assertEqual(synthetic["workloads"], base["synthetic"]["workloads"])
                self.assertTrue(synthetic["delete_at_end"])
                self.assertNotIn(synthetic["dataset"], others | datasets)
                datasets.add(synthetic["dataset"])
                for policy in spec["modes"]["cs"]["policies"]:
                    make_policy(policy)
                self.assertTrue(
                    {"heft-cc", "backlog", "lp-plan", "heft"}
                    <= set(spec["modes"]["cs"]["policies"])
                )

    def test_raw_shard_names_match_the_ingest(self):
        self.assertEqual(campaign.droid_shard_name(217), droid_ingest.shard_name(217))

    def test_the_example_campaigns_fit_forty_hours_and_the_osds(self):
        total = 0
        for spec in (SYNTHETIC, DROID):
            seconds, switching = campaign.estimate(spec)
            total += sum(seconds.values()) + switching
            self.assertLess(
                campaign.peak_gib_per_node(spec), spec["estimate"]["nearfull_gib_per_node"]
            )
            for settings in spec["modes"].values():
                for policy in settings["policies"]:
                    make_policy(policy)
        self.assertLess(total / 3600, 40)
        droid = DROID["droid"]
        shards = len(campaign.parse_range(droid["shards"]))
        self.assertAlmostEqual(shards * DROID["estimate"]["median_shard_bytes"] / 1e9, 500, delta=5)
        _, _, written = campaign.droid_factors(DROID["estimate"])
        wave = droid["wave_shards"] * DROID["estimate"]["median_shard_bytes"]
        self.assertLess(wave * (1 + written) / 2**30, 300)
        self.assertLessEqual(SYNTHETIC["synthetic"]["resident_gib"], 256)


if __name__ == "__main__":
    unittest.main()
