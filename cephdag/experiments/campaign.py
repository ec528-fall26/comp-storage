"""Run a campaign of experiments on the lab from the host: resumable and fail-closed.

    python3 experiments/campaign.py plan SPEC
    python3 experiments/campaign.py run SPEC [--acknowledge-failure]

Run from the repository root on the lab host, outside the containers, with Python 3 and
nothing else. The lab is driven only through

    python3 infra/lab.py --lab LAB stop-workers | mode MODE | shape MBIT | verify|check MODE

and experiments run in the coordinator container as
`docker exec <lab>-n0 python3 -m experiments.<module> ...`, from /app, writing under
/results. Files that containers write are root-owned on the host, so the driver only reads
them; it writes its own files, ledger.jsonl and driver.log, in
<results_host>/campaigns/<name>/, which it creates before any container writes there.

A campaign is a list of steps (`plan` prints them with an hour estimate):

- synthetic: create the resident dataset once (with shaping lifted if `bulk_mbit` is set),
  then for each repeat every case (workload and intensity, in iterations) in each mode of
  the spec in turn, each mode with its policies, cluster file and models. Even repeats run
  the modes in the spec's order, odd ones in reverse, so the runs of a case stay within one
  repeat. Intensities listed in `once` run in the first repeat only. Optionally delete the
  dataset at the end.
- droid: per wave of source shards, ingest the wave from the raw shards (shaping lifted if
  `bulk_mbit` is set), run every mode as above for each of the spec's `variants` (unfused,
  the default, and fused: one droid_fused task per partition), then delete the wave's
  datasets.

Every step is recorded in ledger.jsonl when it starts, completes or fails. A completed step
is never run again; a step that started and did not complete runs again as a new attempt
in a new output directory. After every mode of a case, every row must have passed its
verification with one and the same digest of all outputs; the variants of a DROID wave
must agree on the digest of its videos, statistics and Parquet. Any lab error, failed
command or failed check stops the campaign and records why; `run` refuses to continue
until `--acknowledge-failure` records that someone looked at it.

Before each run step in a new mode the lab is put in that mode and reshaped to
`measure_mbit` (containers that start shape themselves to the lab's configured rate).
Then, if `verify` is set, the first switch of a session and the first one at least
VERIFY_INTERVAL_SECONDS after the last full verify run `verify` (about 3 minutes for cs
and 6.5 for disagg on cephdag8); every other switch runs the light `check`: tbf at the rate
on every active link, the mode's workers answering, Ceph not in HEALTH_ERR and every PG
active+clean. A session starts with stop-workers, so every worker that serves it was
started by it with the spec's `functions`.

Each step's start, completion or failure is also printed, with the time, on stdout.
"""

import argparse
import collections
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
CONTAINER_RESULTS = "/results"
GIB = 2**30
GB = 1e9
DROID_SHARD_COUNT = 2048
VERIFY_INTERVAL_SECONDS = 6 * 3600
COMPUTATIONAL_STORAGE = ("cs", "cs2")


class CampaignStopped(Exception):
    """The campaign cannot go on until someone looks at the recorded reason."""


def droid_shard_name(shard):
    return f"droid_101-train.tfrecord-{shard:05d}-of-{DROID_SHARD_COUNT:05d}"


def parse_range(text):
    first, _, last = text.partition("-")
    return range(int(first), int(last or first) + 1)


def progress(message):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S %Z')} {message}", flush=True)


def mode_order(spec, index):
    """The modes of repeat or wave `index`: in the spec's order if even, reversed if odd."""
    modes = list(spec["modes"])
    return modes if index % 2 == 0 else modes[::-1]


def wave_ranges(shards, per_wave):
    """Consecutive (first, last) shard ranges of at most `per_wave` shards."""
    return [
        (shards[start], shards[min(start + per_wave, len(shards)) - 1])
        for start in range(0, len(shards), per_wave)
    ]


def joined(values):
    return ",".join(str(value) for value in values)


def run_arguments(spec, mode, output, seed):
    settings = spec["modes"][mode]
    arguments = [
        *("--policies", joined(settings["policies"]), "--repeats", "1", "--seed", str(seed)),
        *("--models", settings["models"], "--cluster", settings["cluster"]),
        *("--output", output, "--no-warm"),
    ]
    if spec.get("budget_ms") is not None:
        arguments += ["--budget-ms", str(spec["budget_ms"])]
    return arguments


def data_cluster(spec):
    """The cluster file that datasets are created and deleted with: mode cs's, or else the
    first mode's. Every mode's cluster file lists the same pools at the same nodes."""
    modes = spec["modes"]
    return modes.get("cs", next(iter(modes.values())))["cluster"]


def synthetic_steps(spec):
    synthetic = spec["synthetic"]
    name = synthetic["dataset"]
    placement = joined(synthetic["placement"])
    cluster = data_cluster(spec)
    steps = [
        {
            "id": "create",
            "kind": "bulk",
            "module": "experiments.datasets",
            "arguments": [
                *("create-synthetic", name, "--gib", str(synthetic["resident_gib"])),
                *("--shard-mib", str(synthetic["shard_mib"]), "--placement", placement),
                *("--cluster", cluster),
            ],
        }
    ]
    cases = [
        (workload, iterations)
        for workload in synthetic["workloads"]
        for iterations in synthetic["intensities"]
    ]
    once = synthetic.get("once", [])
    for repeat in range(synthetic["repeats"]):
        for mode in mode_order(spec, repeat):
            for index, (workload, iterations) in enumerate(cases, start=repeat * len(cases)):
                if repeat > 0 and iterations in once:
                    continue
                case = f"{workload['workload']}-i{iterations}-r{repeat}"
                steps.append(
                    {
                        "id": f"{case}-{mode}",
                        "kind": "run",
                        "case": case,
                        "mode": mode,
                        "seed": spec["seed"] + index,
                        "workload": workload,
                        "iterations": iterations,
                        "arguments": [
                            *("--workload", workload["workload"]),
                            *("--iterations", str(iterations)),
                            *("--ratio", str(workload["ratio"])),
                            *("--dataset", name, "--placement", placement),
                            *("--source-gib", str(synthetic["resident_gib"])),
                            *("--dataset-gib", str(workload["gib"])),
                            *("--shard-mib", str(synthetic["shard_mib"])),
                        ],
                    }
                )
    if synthetic.get("delete_at_end"):
        steps.append(
            {
                "id": "delete",
                "kind": "exec",
                "module": "experiments.datasets",
                "arguments": ["delete", name, "--cluster", cluster],
            }
        )
    return steps


def droid_steps(spec):
    droid = spec["droid"]
    shards = list(parse_range(droid["shards"]))
    resolution = str(droid["resolution"])
    cluster = data_cluster(spec)
    steps = []
    for index, (first, last) in enumerate(wave_ranges(shards, droid["wave_shards"])):
        wave = f"wave{index:02d}"
        shard_range = f"{first}-{last}"
        steps.append(
            {
                "id": f"{wave}-ingest",
                "kind": "bulk",
                "module": "experiments.droid_ingest",
                "shards": [first, last],
                "arguments": [
                    *("--shards", shard_range, "--resolution", resolution),
                    *("--placement", joined(droid["placement"])),
                    *("--source-dir", droid["source_dir"], "--cluster", cluster),
                ],
            }
        )
        for variant in droid.get("variants", ["unfused"]):
            case = wave if variant == "unfused" else f"{wave}-{variant}"
            arguments = [
                *("--workload", "droid", "--droid-shards", shard_range),
                *("--resolution", resolution, "--wave-gib", "1000000"),
            ]
            if variant == "fused":
                arguments.append("--droid-fused")
            for mode in mode_order(spec, index):
                steps.append(
                    {
                        "id": f"{case}-{mode}",
                        "kind": "run",
                        "case": case,
                        "wave": wave,
                        "fused": variant == "fused",
                        "mode": mode,
                        "seed": spec["seed"] + index,
                        "shards": [first, last],
                        "arguments": arguments,
                    }
                )
        steps.append(
            {
                "id": f"{wave}-delete",
                "kind": "exec",
                "module": "experiments.datasets",
                "arguments": [
                    *("delete", "--droid-shards", shard_range, "--resolution", resolution),
                    *("--cluster", cluster),
                ],
            }
        )
    return steps


def steps_of(spec):
    if spec["kind"] == "synthetic":
        return synthetic_steps(spec)
    if spec["kind"] == "droid":
        return droid_steps(spec)
    raise ValueError(f"unknown campaign kind {spec['kind']}")


def host_path(spec, container_path):
    """The host path of a path under /results in the containers."""
    relative = Path(container_path).relative_to(CONTAINER_RESULTS)
    return Path(spec["results_host"]) / relative


def lab_name(spec):
    return json.loads((REPO / spec["lab"]).read_text())["name"]


def campaign_directory(spec):
    return Path(spec["results_host"]) / "campaigns" / spec["name"]


# Estimate: per-task serial time (read, compute, write; one task at a time per worker)
# summed over the tasks and divided by the workers. cs and cs2 read and write on the local
# disk, as data-local placement would; disagg and disagg2 move every byte over one link.


def synthetic_profile(workload, ratio, iterations):
    """(tasks, bytes read, bytes written, largest-input bytes x iterations) per source shard
    byte, from run.build_workload."""
    if workload == "independent":
        return 1, 1.0, ratio, float(iterations)
    if workload == "competing":
        return 6, 2 + 5.5 * ratio, 4.875 * ratio, iterations * (5 + 7 * ratio)
    raise ValueError(f"no estimate for workload {workload}")


def serial_seconds(rates, mode, read_bytes, write_bytes, compute_seconds):
    if mode in COMPUTATIONAL_STORAGE:
        io = read_bytes / rates["read_bps"] + write_bytes / rates["write_bps"]
    else:
        io = (read_bytes + write_bytes) / rates["link_bps"]
    return (io + compute_seconds) / rates["workers"] + rates["run_overhead_seconds"]


def synthetic_run_seconds(spec, step):
    rates = spec["estimate"]
    workload = step["workload"]
    source = workload["gib"] * GIB
    tasks, read, write, iteration = synthetic_profile(
        workload["workload"], workload["ratio"], step["iterations"]
    )
    fixed, per_input, per_iteration, per_output = rates["map_coefficients"]
    shards = source / (spec["synthetic"]["shard_mib"] * 2**20)
    compute = fixed * tasks * shards + source * (
        read * per_input + iteration * per_iteration + write * per_output
    )
    return serial_seconds(rates, step["mode"], source * read, source * write, compute)


def droid_factors(rates, fused=False):
    """(bytes read, bytes written, output bytes) per source byte of the DROID graph; the fused
    graph stores no frames."""
    frames = rates["frames_per_source"]
    if fused:
        videos = frames * rates["video_per_frames"]
        return 1, videos, videos
    written = frames * (1 + rates["video_per_frames"])
    return 1 + 2 * frames, written, written


def droid_wave_bytes(spec, first, last):
    """Source bytes of shards first..last: the raw files' sizes, or the median per shard."""
    raw = host_path(spec, spec["droid"]["source_dir"])
    total = 0
    for shard in range(first, last + 1):
        path = raw / droid_shard_name(shard)
        total += path.stat().st_size if path.exists() else spec["estimate"]["median_shard_bytes"]
    return total


def droid_run_seconds(spec, step):
    rates = spec["estimate"]
    source = droid_wave_bytes(spec, *step["shards"])
    read, write, _ = droid_factors(rates, step["fused"])
    compute = source / GB * rates["droid_cpu_seconds_per_source_gb"]
    return serial_seconds(rates, step["mode"], source * read, source * write, compute)


def step_seconds(spec, step):
    rates = spec["estimate"]
    if step["kind"] == "run":
        policies = len(spec["modes"][step["mode"]]["policies"])
        if spec["kind"] == "synthetic":
            return policies * synthetic_run_seconds(spec, step)
        return policies * droid_run_seconds(spec, step)
    if step["id"] == "create":
        return spec["synthetic"]["resident_gib"] * GIB / rates["bulk_write_bps"]
    if step["kind"] == "bulk":
        return droid_wave_bytes(spec, *step["shards"]) / rates["ingest_bps"]
    return rates["delete_seconds"]


def estimate(spec):
    """(seconds of each step, seconds of mode switches and full verifies) for the whole
    campaign run in one session."""
    rates = spec["estimate"]
    steps = steps_of(spec)
    seconds = {step["id"]: step_seconds(spec, step) for step in steps}
    modes = [step["mode"] for step in steps if step["kind"] == "run"]
    switches = 1 + sum(1 for before, after in zip(modes, modes[1:]) if before != after)
    verifies = 0
    if spec.get("verify", True):
        verifies = 1 + int(sum(seconds.values()) // VERIFY_INTERVAL_SECONDS)
    return seconds, switches * rates["switch_seconds"] + verifies * rates["verify_seconds"]


def peak_gib_per_node(spec):
    """Largest data on one node during the campaign if every node holds an equal share."""
    rates = spec["estimate"]
    nodes = len(spec["synthetic" if spec["kind"] == "synthetic" else "droid"]["placement"])
    if spec["kind"] == "synthetic":
        synthetic = spec["synthetic"]
        outputs = max(
            workload["gib"] * synthetic_profile(workload["workload"], workload["ratio"], 1)[2]
            for workload in synthetic["workloads"]
        )
        return (synthetic["resident_gib"] + outputs) / nodes
    _, _, written = droid_factors(rates)
    largest = max(
        droid_wave_bytes(spec, *step["shards"]) for step in steps_of(spec) if step["kind"] == "bulk"
    )
    return largest * (1 + written) / GIB / nodes


def preflight(spec):
    """Problems that would make the campaign fail or overfill the cluster."""
    problems = []
    for mode, settings in spec["modes"].items():
        if not (REPO / settings["cluster"]).exists():
            problems.append(f"{mode}: cluster file {settings['cluster']} is missing")
        models = host_path(spec, settings["models"])
        if not models.exists():
            problems.append(f"{mode}: models {models} are missing; calibrate first")
        elif spec["kind"] == "droid":
            functions = json.loads(models.read_text())["compute"].get("functions", {})
            if len(functions) < 4:
                problems.append(f"{mode}: models {models} have no DROID function fit")
            elif "fused" in spec["droid"].get("variants", []) and "droid_fused@1" not in functions:
                problems.append(f"{mode}: models {models} have no droid_fused@1 cost")
    if spec["kind"] == "droid":
        raw = host_path(spec, spec["droid"]["source_dir"])
        missing = [
            shard
            for shard in parse_range(spec["droid"]["shards"])
            if not (raw / droid_shard_name(shard)).exists()
        ]
        if missing:
            problems.append(f"{len(missing)} raw shards missing in {raw}, first {missing[0]}")
    cap = spec["estimate"]["nearfull_gib_per_node"]
    peak = peak_gib_per_node(spec)
    if peak > cap:
        problems.append(f"{peak:.1f} GiB per node at the peak, above {cap} GiB")
    return problems


class Ledger:
    """Append-only JSON lines: started, completed, failed and acknowledged events."""

    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock

    def events(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

    def append(self, **event):
        with open(self.path, "a") as ledger:
            ledger.write(json.dumps({"time": self.clock(), **event}) + "\n")

    def completed(self):
        return {event["step"]: event for event in self.events() if event["event"] == "completed"}

    def attempts(self, step):
        return sum(
            1 for event in self.events() if event["event"] == "started" and event["step"] == step
        )

    def unresolved_failure(self):
        """The last failure, unless it was acknowledged after it."""
        failure = None
        for event in self.events():
            if event["event"] == "failed":
                failure = event
            elif event["event"] == "acknowledged":
                failure = None
        return failure


class StepFailed(Exception):
    pass


def run_command(argv, log):
    """Run `argv` from the repository root, appending its output to `log`; return
    (exit code, its last lines)."""
    tail = collections.deque(maxlen=20)
    with open(log, "a") as out:
        out.write(f"$ {' '.join(argv)}\n")
        out.flush()
        process = subprocess.Popen(
            argv, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        for line in process.stdout:
            out.write(line)
            out.flush()
            tail.append(line.rstrip("\n"))
        code = process.wait()
        out.write(f"# exit {code}\n")
    return code, "\n".join(tail)


class Campaign:
    def __init__(self, spec, runner=run_command, clock=time.time, say=progress):
        self.spec = spec
        self.runner = runner
        self.say = say
        self.directory = campaign_directory(spec)
        self.log = self.directory / "driver.log"
        self.ledger = Ledger(self.directory / "ledger.jsonl", clock)
        self.clock = clock
        self.container = f"{lab_name(spec)}-n0"
        self.mode = None
        self.verified_at = None

    def command(self, argv, what):
        code, tail = self.runner(argv, self.log)
        if code != 0:
            raise StepFailed(f"{what} exited {code}: {tail[-2000:]}")

    def lab(self, *arguments):
        argv = ["python3", "infra/lab.py", "--lab", self.spec["lab"], *map(str, arguments)]
        self.command(argv, "lab " + " ".join(map(str, arguments)))

    def experiment(self, module, arguments):
        argv = ["docker", "exec", self.container, "python3", "-m", module, *arguments]
        self.command(argv, module)

    def switch(self, mode):
        if mode == self.mode:
            return
        self.mode = None
        functions = self.spec.get("functions", [])
        self.lab("mode", mode, *(["--functions", *functions] if functions else []))
        self.lab("shape", self.spec["measure_mbit"])
        if self.spec.get("verify", True) and self.verify_due():
            self.lab("verify", mode)
            self.verified_at = self.clock()
        else:
            self.lab("check", mode)
        self.mode = mode

    def verify_due(self):
        if self.verified_at is None:
            return True
        return self.clock() - self.verified_at >= VERIFY_INTERVAL_SECONDS

    def bulk(self, step):
        if self.spec.get("bulk_mbit"):
            self.lab("shape", self.spec["bulk_mbit"])
        try:
            self.experiment(step["module"], step["arguments"])
        finally:
            if self.spec.get("bulk_mbit"):
                self.lab("shape", self.spec["measure_mbit"])

    def run_step(self, step, output):
        self.switch(step["mode"])
        arguments = step["arguments"] + run_arguments(self.spec, step["mode"], output, step["seed"])
        self.experiment("experiments.run", arguments)
        return self.check_rows(step, output)

    def check_rows(self, step, output):
        """The digest of the step's outputs, after checking its rows and its partner's."""
        path = host_path(self.spec, output) / "rows.jsonl"
        if not path.exists():
            raise StepFailed(f"{path} is missing")
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        policies = self.spec["modes"][step["mode"]]["policies"]
        if sorted(row["policy"] for row in rows) != sorted(policies):
            raise StepFailed(f"{path}: rows for {[row['policy'] for row in rows]}, not {policies}")
        failed = [row["policy"] for row in rows if not row["verification"]["passed"]]
        if failed:
            raise StepFailed(f"{path}: verification failed for {failed}")
        if {row["mode"] for row in rows} != {step["mode"]}:
            raise StepFailed(f"{path}: rows are not all in mode {step['mode']}")
        digests = {row["verification"]["outputs_sha256"] for row in rows}
        finals = {row["verification"].get("final_outputs_sha256") for row in rows}
        if len(digests) != 1 or len(finals) != 1:
            raise StepFailed(f"{path}: policies disagree on the outputs")
        (digest,) = digests
        (final,) = finals
        for other in self.ledger.completed().values():
            if other.get("case") == step["case"] and other["outputs_sha256"] != digest:
                raise StepFailed(
                    f"{step['id']} outputs {digest} differ from {other['step']} "
                    f"{other['outputs_sha256']}"
                )
            if final and other.get("wave") == step.get("wave"):
                if other.get("final_outputs_sha256") not in (None, final):
                    raise StepFailed(
                        f"{step['id']} videos, statistics and Parquet {final} differ from "
                        f"{other['step']} {other['final_outputs_sha256']}"
                    )
        return {"outputs_sha256": digest, "final_outputs_sha256": final}

    def run(self, acknowledge=False):
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / "driver.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CampaignStopped(f"another driver runs campaign {self.spec['name']}") from None
            self.run_locked(acknowledge)

    def run_locked(self, acknowledge):
        failure = self.ledger.unresolved_failure()
        if failure is not None:
            if not acknowledge:
                raise CampaignStopped(
                    f"step {failure['step']} failed: {failure['reason']}; look at it, then "
                    "run again with --acknowledge-failure"
                )
            self.ledger.append(event="acknowledged", step=failure["step"])
        problems = preflight(self.spec)
        if problems:
            raise CampaignStopped("; ".join(problems))
        completed = self.ledger.completed()
        steps = steps_of(self.spec)
        pending = [step for step in steps if step["id"] not in completed]
        self.say(f"{self.spec['name']}: {len(steps) - len(pending)} of {len(steps)} done")
        if not pending:
            return
        try:
            self.lab("stop-workers")
            self.lab("shape", self.spec["measure_mbit"])
        except (StepFailed, OSError) as error:
            self.ledger.append(event="failed", step="session", reason=str(error))
            raise CampaignStopped(str(error)) from error
        for step in pending:
            self.execute(step)
        self.say(f"{self.spec['name']}: complete")

    def execute(self, step):
        attempt = self.ledger.attempts(step["id"]) + 1
        output = f"{CONTAINER_RESULTS}/campaigns/{self.spec['name']}/{step['id']}/a{attempt}"
        self.ledger.append(event="started", step=step["id"], attempt=attempt, output=output)
        self.say(f"{step['id']}: attempt {attempt} started")
        began = time.monotonic()
        try:
            if step["kind"] == "run":
                digests = self.run_step(step, output)
                record = {"case": step["case"], "mode": step["mode"], **digests}
                if "wave" in step:
                    record["wave"] = step["wave"]
            elif step["kind"] == "bulk":
                self.bulk(step)
                record = {}
            else:
                self.experiment(step["module"], step["arguments"])
                record = {}
        except (StepFailed, OSError) as error:
            self.ledger.append(event="failed", step=step["id"], attempt=attempt, reason=str(error))
            self.say(f"{step['id']}: attempt {attempt} failed: {str(error)[:300]}")
            raise CampaignStopped(f"{step['id']}: {error}") from error
        seconds = time.monotonic() - began
        self.ledger.append(
            event="completed",
            step=step["id"],
            attempt=attempt,
            output=output,
            seconds=seconds,
            **record,
        )
        self.say(f"{step['id']}: attempt {attempt} completed in {seconds:.0f} s")


def describe(spec):
    seconds, switching = estimate(spec)
    lines = [f"{'step':<32} {'hours':>7}"]
    for step, value in seconds.items():
        lines.append(f"{step:<32} {value / 3600:7.2f}")
    lines.append(f"{'mode switches and verifies':<32} {switching / 3600:7.2f}")
    total = sum(seconds.values()) + switching
    lines.append(f"{'total':<32} {total / 3600:7.2f}")
    lines.append(f"peak per node, balanced: {peak_gib_per_node(spec):.1f} GiB")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["plan", "run"])
    parser.add_argument("spec", type=Path)
    parser.add_argument("--acknowledge-failure", action="store_true")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    if args.command == "plan":
        print(describe(spec))
        for problem in preflight(spec):
            print(f"problem: {problem}")
        return
    try:
        Campaign(spec).run(args.acknowledge_failure)
    except CampaignStopped as stopped:
        print(f"stopped: {stopped}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
