"""The coordinator: observe the workers, ask the policy, dispatch, acknowledge, repeat.

All worker traffic is JSON over HTTP: /state to observe, /submit to dispatch and /ack
to release a durably published completion. A policy sees the live cluster as a
model.Simulation rebuilt from these observations at every event.
"""

from bisect import bisect_left, insort
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.client import IncompleteRead, RemoteDisconnected
import json
import math
import time
from urllib.error import HTTPError
import urllib.request
import uuid

from .graph import ObjectRef, OutputRef, ref_from_dict
from .model import InFlight, Simulation, Stage
from .policies import make_policy
from .policies.greedy import forecast_finish

# The fields of a finished job that add_running reads.
OBSERVED_JOB_FIELDS = ("finished", "stage_events", "stage_index", "stage_started")
AMBIGUOUS_FAILURES = (
    OSError,
    IncompleteRead,
    RemoteDisconnected,
    json.JSONDecodeError,
    UnicodeDecodeError,
)


class HttpTransport:
    """POST a JSON payload to a node's worker; the lab routes are private, so no proxies."""

    def __init__(self, cluster, timeout=15):
        self.urls = [node.worker for node in cluster.nodes]
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, node, path, payload):
        request = urllib.request.Request(
            f"{self.urls[node]}/{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            return json.load(response)


def post_with_retry(transport, node, path, payload, attempts=3):
    """Resend the same payload after an ambiguous transport failure, never after a rejection."""
    for attempt in range(attempts):
        try:
            return transport.post(node, path, payload)
        except HTTPError:
            raise
        except AMBIGUOUS_FAILURES:
            if attempt == attempts - 1:
                raise


def acknowledge(transport, node, job):
    """Let the worker drop a consumed, durably published job from memory."""
    response = post_with_retry(
        transport, node, "ack", {"key": job["key"], "token": job["ack_token"]}
    )
    if response != {"key": job["key"], "acknowledged": True}:
        raise RuntimeError("invalid ACK response")


def total_transmit_bytes(before, after):
    """Bytes sent by all nodes between two counter snapshots (receivers are not added again)."""
    if len(before) != len(after):
        raise ValueError("counter node sets differ")
    deltas = [later["tx"] - earlier["tx"] for earlier, later in zip(before, after)]
    if any(delta < 0 for delta in deltas):
        raise ValueError("interface counters reset during run")
    return sum(deltas)


def host_snapshots(cluster, snapshots):
    """The /state snapshots of each host's first worker, which count its interface once."""
    return [snapshot for snapshot in snapshots if snapshot["node"] in cluster.interface_nodes]


def observed_state(
    graph,
    models,
    pools,
    completed,
    pending,
    jobs,
    assigned,
    *,
    now,
    elapsed,
    history=None,
    workers=None,
    hosts=None,
):
    """Rebuild a Simulation of the live cluster from worker observations.

    `pending` maps each running task to its Dispatch and `jobs` holds the latest worker
    record of every task. `history` maps every dispatched task to its Dispatch. The
    coordinator keeps the same picture incrementally (Run.observe); this function is the
    reference it is tested against.
    """
    state = Simulation(graph, models, pools, workers=workers, hosts=hosts)
    state.completed = completed
    state.assigned_work = list(assigned)
    state.time = elapsed
    add_running(state, pending, jobs, (history or pending).items(), now=now)
    state.refresh()
    return state


def earliest_stage_start(pending, jobs, now):
    return min((jobs.get(task, {}).get("stage_started", now) for task in pending), default=now)


def add_running(state, pending, jobs, dispatched, *, now):
    """Put every pending task into `state.running` with its current stage's observed progress.

    The progress of a running stage is estimated from the stages that overlapped it, so
    concurrent flows share bandwidth as in the forecast. `dispatched` lists (task, Dispatch)
    for tasks whose stages may have overlapped: any that finished before the earliest
    current stage began are ignored.
    """
    earliest = earliest_stage_start(pending, jobs, now)
    observed = []
    for task, action in dispatched:
        job = jobs.get(task, {})
        if job.get("finished", now) < earliest:
            continue
        stages = state.stages_for(action)
        events = job.get("stage_events") or [
            {
                "index": job.get("stage_index", 0),
                "start": job.get("stage_started", now),
                "end": None,
            }
        ]
        for event in events:
            end = event.get("end") if event.get("end") is not None else now
            if end <= earliest or event["start"] >= now:
                continue
            index = event["index"]
            resources = (
                stages[index].resources if index < len(stages) else (f"cpu:{action.worker}",)
            )
            observed.append((event["start"], end, resources))
    for task, action in pending.items():
        job = jobs.get(task, {"stage_index": 0, "stage_started": now})
        stages = state.stages_for(action)
        index = job["stage_index"]
        if index >= len(stages):
            stages = [Stage("publish", 0.001, (f"cpu:{action.worker}",))]
        else:
            stages = stages[index:]
            begin = job["stage_started"]
            boundaries = {begin, now}
            for start, end, _ in observed:
                if end > begin and start < now:
                    boundaries.update((max(begin, start), min(now, end)))
            progress = 0.0
            rate = min(state.capacity[resource] for resource in stages[0].resources)
            ordered = sorted(boundaries)
            for left, right in zip(ordered, ordered[1:]):
                midpoint = (left + right) / 2
                usage = Counter(
                    resource
                    for start, end, resources in observed
                    if start <= midpoint < end
                    for resource in resources
                )
                rate = min(
                    state.capacity[resource] / max(1, usage[resource])
                    for resource in stages[0].resources
                )
                progress += (right - left) * rate
            # A stage the worker still reports as active cannot have finished yet.
            stages[0].remaining = max(rate * 0.001, stages[0].remaining - progress)
        state.running[task] = InFlight(action, stages, index)


def run(
    graph,
    models,
    scheduler,
    *,
    cluster,
    store,
    transport,
    run_id=None,
    timeout=7200,
    scheduler_options=None,
):
    """Execute `graph` on the cluster and return the metrics record (also stored in RADOS)."""
    return Run(
        graph, models, scheduler, cluster, store, transport, run_id, timeout, scheduler_options
    ).execute()


class Run:
    def __init__(
        self,
        graph,
        models,
        scheduler,
        cluster,
        store,
        transport,
        run_id,
        timeout,
        scheduler_options,
    ):
        self.graph = graph
        self.models = models
        self.scheduler = scheduler
        self.scheduler_options = scheduler_options or {}
        self.cluster = cluster
        self.store = store
        self.transport = transport
        self.run_id = run_id or uuid.uuid4().hex
        self.timeout = timeout
        self.completed = {}
        self.pending = {}
        self.history = {}
        self.jobs = {}
        # The cluster without running tasks: completed outputs and the ready set.
        self.live = None
        # (finish time, task) of completed tasks, sorted, and the parts of their last
        # worker record that observing later stages still needs.
        self.finish_times = []
        self.finished_jobs = {}
        self.records = {}
        self.payloads = {}
        self.decisions = []
        self.assigned = [0.0] * len(cluster.nodes)
        self.scheduler_seconds = 0.0
        self.search_iterations = 0
        self.dispatches = 0
        self.waits = 0
        self.payload_bytes = 0
        self.observed_events = 0

    def elapsed(self):
        return time.perf_counter() - self.start

    def execute(self):
        self.start = time.perf_counter()
        self.graph.freeze()
        self._check_cluster()
        self.live = Simulation(
            self.graph,
            self.models,
            self.cluster.pools,
            workers=self.cluster.worker_nodes,
            hosts=self.cluster.hosts,
        )
        policy_start = time.perf_counter()
        if isinstance(self.scheduler, str):
            self.policy = make_policy(self.scheduler, **self.scheduler_options)
        else:
            self.policy = self.scheduler
        self.policy_init_seconds = time.perf_counter() - policy_start
        self.scheduler_seconds = self.policy_init_seconds
        with ThreadPoolExecutor(max_workers=len(self.cluster.nodes)) as self.executor:
            before = self._poll()
            if any(snapshot["active"] for snapshot in before):
                raise RuntimeError("cluster has an active prior job")
            self._save("graph.json", self.graph.to_dict())
            try:
                self._schedule()
                wall = self.elapsed()
                after = self._poll()
                result = self._result(wall, before, after)
                verify_start = time.perf_counter()
                for output in result["final_outputs"]:
                    self.store.read(ref_from_dict(output))
                result["verification_seconds"] = time.perf_counter() - verify_start
                result["verified_final_bytes"] = sum(
                    output["size"] for output in result["final_outputs"]
                )
                self._save("result.json", result)
                return result
            except Exception as error:
                self._save(
                    "failed.json",
                    {
                        "error": f"{type(error).__name__}: {error}",
                        "completed": list(self.completed),
                        "pending": list(self.pending),
                        "pending_payloads": {task: self.payloads[task] for task in self.pending},
                    },
                )
                raise

    def _check_cluster(self):
        if self.models.node_count != len(self.cluster.nodes):
            raise ValueError(
                f"models describe {self.models.node_count} nodes, "
                f"the cluster has {len(self.cluster.nodes)}"
            )
        for task in self.graph.tasks.values():
            for ref in task.inputs:
                if isinstance(ref, ObjectRef) and ref.pool not in self.cluster.pools:
                    raise ValueError(
                        f"input {ref.key} is in pool {ref.pool}, which no cluster node owns"
                    )

    def _save(self, name, value):
        self.store.put_manifest(self.cluster.metadata_pool, f"runs/{self.run_id}/{name}", value)

    def _poll(self):
        """One /state snapshot per worker, asking about the tasks pending on that worker."""
        futures = []
        for node in self.cluster.worker_nodes:
            keys = [
                f"{self.run_id}/{task}"
                for task, action in self.pending.items()
                if action.worker == node
            ]
            futures.append(self.executor.submit(self.transport.post, node, "state", {"keys": keys}))
        return [future.result() for future in futures]

    def _schedule(self):
        last_signature = None
        while len(self.completed) < len(self.graph.tasks):
            if self.elapsed() > self.timeout:
                raise TimeoutError("DAG run timeout")
            self._absorb(self._poll())
            signature = tuple(
                (task, self.jobs.get(task, {}).get("stage_index")) for task in self.pending
            )
            if signature != last_signature:
                self.observed_events += 1
                last_signature = signature
            if len(self.completed) == len(self.graph.tasks):
                break
            state = self.observe(now=time.perf_counter(), elapsed=self.elapsed())
            action, seconds = self._decide(state)
            if action is None:
                if not self.pending:
                    raise RuntimeError("scheduler cannot dispatch remaining ready tasks")
                self.waits += 1
                self._wait_for_change()
            else:
                self._dispatch(state, action, seconds)

    def observe(self, *, now, elapsed):
        """The same Simulation as observed_state(), from the live picture and recent jobs only."""
        state = self.live.clone()
        state.assigned_work = list(self.assigned)
        state.time = elapsed
        earliest = earliest_stage_start(self.pending, self.jobs, now)
        recent = [
            task for _, task in self.finish_times[bisect_left(self.finish_times, (earliest,)) :]
        ]
        jobs = {**self.jobs, **{task: self.finished_jobs[task] for task in recent}}
        dispatched = [(task, self.history[task]) for task in recent]
        add_running(state, self.pending, jobs, dispatched + list(self.pending.items()), now=now)
        return state

    def _absorb(self, snapshots):
        """Record every job update; collect and acknowledge completed tasks."""
        for snapshot in snapshots:
            for key, job in snapshot["jobs"].items():
                task = key.split("/")[1]
                self.jobs[task] = job
                if job["status"] == "publication_failed":
                    raise RuntimeError(
                        f"{task}: worker completion publication failed: {job['error']}"
                    )
                if job["status"] == "failed":
                    acknowledge(self.transport, snapshot["node"], job)
                    raise RuntimeError(f"{task}: {job['error']}")
                if job["status"] == "completed" and task in self.pending:
                    result = job["result"]
                    self.completed[task] = tuple(ref_from_dict(ref) for ref in result["outputs"])
                    self.records[task].update(result=result, actual_finish=self.elapsed())
                    acknowledge(self.transport, snapshot["node"], job)
                    del self.pending[task]
                    del self.payloads[task]
                    del self.jobs[task]
                    self.live.observe_completion(task, self.completed[task])
                    insort(self.finish_times, (job.get("finished", math.inf), task))
                    self.finished_jobs[task] = {
                        key: job[key] for key in OBSERVED_JOB_FIELDS if key in job
                    }

    def _decide(self, state):
        decision_start = time.perf_counter()
        action = self.policy.choose(state)
        seconds = time.perf_counter() - decision_start
        info = dict(getattr(self.policy, "last_info", {}))
        self.scheduler_seconds += seconds
        self.search_iterations += info.get("iterations", 0)
        self.decisions.append(
            {
                "at_seconds": self.elapsed(),
                "seconds": seconds,
                "action": None
                if action is None
                else {
                    "task": action.task,
                    "worker": action.worker,
                    "targets": list(action.targets),
                },
                "search": info,
            }
        )
        return action, seconds

    def _wait_for_change(self):
        """Waiting means waiting for the next observed stage or status change, not a fixed delay."""
        while True:
            time.sleep(0.005)
            current = self._poll()
            new = tuple(
                sorted(
                    (key, job["status"], job.get("stage_index"))
                    for snapshot in current
                    for key, job in snapshot["jobs"].items()
                )
            )
            old = tuple(
                sorted(
                    (
                        f"{self.run_id}/{task}",
                        self.jobs[task]["status"],
                        self.jobs[task].get("stage_index"),
                    )
                    for task in self.pending
                    if task in self.jobs
                )
            )
            if new != old or self.elapsed() > self.timeout:
                return

    def _dispatch(self, state, action, decision_seconds):
        predicted_finish = forecast_finish(state, action).time
        self.payload_bytes += state.transfer_bytes(action)
        self.assigned[action.worker] += state.isolated_seconds(action)
        task = self.graph.tasks[action.task]
        inputs = [state.resolve(ref) for ref in task.inputs]
        sized = task.sized_for([stored.size for stored in inputs])
        payload = {
            "run_id": self.run_id,
            "worker": action.worker,
            "targets": list(action.targets),
            "inputs": [stored.to_dict() for stored in inputs],
            "task": sized.to_dict(),
        }
        # Record the task as possibly accepted before the first transport attempt.
        self.pending[action.task] = action
        self.payloads[action.task] = payload
        self.history[action.task] = action
        self.records[action.task] = {
            "worker": action.worker,
            "targets": list(action.targets),
            "submitted": self.elapsed(),
            "predicted_finish": predicted_finish,
            "decision_seconds": decision_seconds,
        }
        self.live.observe_dispatch(action.task)
        self.jobs[action.task] = post_with_retry(self.transport, action.worker, "submit", payload)
        self.dispatches += 1

    def _result(self, wall, before, after):
        consumed = {
            (ref.task, ref.output)
            for task in self.graph.tasks.values()
            for ref in task.inputs
            if isinstance(ref, OutputRef)
        }
        final_outputs = [
            ref.to_dict()
            for task, refs in self.completed.items()
            for index, ref in enumerate(refs)
            if (task, index) not in consumed
        ]
        scheduler = (
            self.scheduler if isinstance(self.scheduler, str) else type(self.scheduler).__name__
        )
        backend = getattr(self.store, "backend", "unknown")
        return {
            "schema": 1,
            "run_id": self.run_id,
            "scheduler": scheduler,
            "system_benchmark": backend == "ceph-rados",
            "storage_backend": backend,
            "wall_seconds": wall,
            "scheduler_seconds": self.scheduler_seconds,
            "policy_init_seconds": self.policy_init_seconds,
            "search_iterations": self.search_iterations,
            "dispatches": self.dispatches,
            "waits": self.waits,
            "decisions": self.decisions,
            "scheduler_options": self.scheduler_options,
            "observed_stage_events": self.observed_events,
            "application_remote_bytes": self.payload_bytes,
            "network_tx_bytes": total_transmit_bytes(
                [snapshot["counters"] for snapshot in host_snapshots(self.cluster, before)],
                [snapshot["counters"] for snapshot in host_snapshots(self.cluster, after)],
            ),
            "counter_before": before,
            "counter_after": after,
            "tasks": self.records,
            "final_outputs": final_outputs,
            "models": self.models.to_dict(),
        }
