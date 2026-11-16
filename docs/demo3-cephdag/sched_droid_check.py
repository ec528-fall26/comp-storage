"""Whether each sched-droid policy completes the fused DROID graph with node 0 holding 9 of
every 16 partitions, and what it forecasts, in-process with the cs fused models; no lab.

    cd cephdag && .venv/bin/python -I ../demo3/sched_droid_check.py MODELS OUT_JSON [CONFIG ...]

Without CONFIG (before the run), the graph mirrors a sched-droid wave: shards 500-507 or 508-515 at their raw sizes, each cut
into partitions of about 31 MB (sensitivity-droid's mean: 88 partitions for 2.74 GB), node
of partition i of shard s = PATTERN[(s + i) % 16] as droid_ingest's CyclicPlacement does,
steps set so the frames are 2.16x the source, one droid_fused task per partition
(droid_workload.build_fused_graph). Each policy runs to completion in model.Simulation
("equal") and in sched_forecast's FairSimulation ("fair").

With CONFIG, the config.json of a sched-droid run (wave-0000/config.json), the graph is the
measured one: its partitions, their pools and steps, built as experiments/run.py builds it.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cephdag import droid
from cephdag.graph import Dataset, ObjectRef, ref_from_dict
from cephdag.model import Models
from experiments import droid_workload

from sched_forecast import POOLS, SIMULATIONS, forecast

PATTERN = (0,) * 9 + tuple(range(1, 8))
PARTITION_BYTES = 2737934595 / 88
FRAMES_PER_SOURCE = 2.16
SHARD_BYTES = {
    500: 977034844,
    501: 1221106993,
    502: 806699696,
    503: 728458968,
    504: 739555039,
    505: 966376264,
    506: 549244438,
    507: 945364838,
    508: 955258766,
    509: 1141351404,
    510: 807141588,
    511: 851466855,
    512: 811951987,
    513: 745153101,
    514: 896309817,
    515: 566678469,
}
WAVES = {"wave00": range(500, 508), "wave01": range(508, 516)}
POLICIES = ("data-local", "heft-cc", "lp-plan2", "heft")


def wave_graph(shards):
    refs = []
    steps = []
    for shard in shards:
        count = max(1, round(SHARD_BYTES[shard] / PARTITION_BYTES))
        size = SHARD_BYTES[shard] // count
        for part in range(count):
            node = PATTERN[(shard + part) % len(PATTERN)]
            refs.append(ObjectRef(POOLS[node], f"droid/{shard}/{part}", size, "droid-episodes"))
            steps.append(max(1, round(FRAMES_PER_SOURCE * size / droid.frame_bytes(1, 128))))
    refs = [
        ObjectRef(ref.pool, ref.key, ref.size, ref.dtype, index) for index, ref in enumerate(refs)
    ]
    dataset = Dataset("droid-skew", tuple(refs))
    return droid_workload.build_fused_graph(dataset, 128, steps), refs


def measured_graph(config_path):
    config = json.loads(Path(config_path).read_text())
    refs = [ref_from_dict(shard) for shard in config["dataset"]["shards"]]
    dataset = Dataset(config["dataset"]["name"], tuple(refs))
    graph = droid_workload.build_fused_graph(dataset, 128, config["case"]["steps"])
    return graph, refs


def main():
    models = Models.from_dict(json.loads(Path(sys.argv[1]).read_text()))
    output = Path(sys.argv[2])
    configs = sys.argv[3:]
    if configs:
        graphs = {f"measured-{index}": measured_graph(path) for index, path in enumerate(configs)}
    else:
        graphs = {wave: wave_graph(shards) for wave, shards in WAVES.items()}
    results = {}
    for wave, (graph, refs) in graphs.items():
        on_node0 = sum(ref.size for ref in refs if ref.pool == POOLS[0])
        total = sum(ref.size for ref in refs)
        results[f"{wave}/graph"] = {
            "tasks": len(graph.tasks),
            "source_bytes": total,
            "node0_share": on_node0 / total,
        }
        print(wave, json.dumps(results[f"{wave}/graph"]), flush=True)
        for name in POLICIES:
            for sharing, simulation in SIMULATIONS.items():
                key = f"{sharing}/{wave}/{name}"
                results[key] = forecast(name, graph, models, simulation)
                print(key, json.dumps(results[key]), flush=True)
                output.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
