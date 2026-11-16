"""Recompute sampled outputs with scalar float32 arithmetic, independent of the numpy kernel."""

import math
import struct

import numpy as np

from cephdag.graph import ObjectRef, OutputRef, ref_from_dict

BUILTIN_FUNCTIONS = ("map@1", "join@1", "split@1")
PREFIX = 16


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def expected_prefix(graph, ref, seed, cache):
    """The first (up to) 16 float32 values that `ref` must hold."""
    if ref in cache:
        return cache[ref]
    count = min(PREFIX, graph.size(ref) // 4)
    if isinstance(ref, ObjectRef):
        source = np.random.default_rng(seed + ref.partition).random(count, dtype=np.float32)
        values = [float(value) for value in source]
    else:
        task = graph.tasks[ref.task]
        if task.function not in BUILTIN_FUNCTIONS:
            raise ValueError("no independent oracle for a custom function")
        inputs = [expected_prefix(graph, parent, seed, cache) for parent in task.inputs]
        base_size = max((graph.size(parent) // 4 for parent in task.inputs), default=0)
        base = []
        for index in range(min(PREFIX, base_size)):
            value = 0.0
            for item in inputs:
                value = f32(value + (item[index % len(item)] if item else 0.0))
            value = f32(value / len(inputs))
            for _ in range(task.params["iterations"]):
                value = f32(value * f32(1.0001))
                value = f32(value + f32(0.0001))
                value = f32(math.tanh(value))
            base.append(value)
        values = [base[index % base_size] for index in range(count)] if base_size else []
    cache[ref] = values
    return values


def verify_oracle(store, result, graph, seed, max_outputs=5):
    """Check up to `max_outputs` final outputs, spread over partitions; raise on any mismatch."""
    cache = {}
    origin = {
        output["key"]: (task_id, index)
        for task_id, entry in result["tasks"].items()
        for index, output in enumerate(entry["result"]["outputs"])
    }
    finals = sorted(
        result["final_outputs"], key=lambda output: (output["partition"], output["key"])
    )
    count = min(max_outputs, len(finals))
    indices = sorted(
        {round(position * (len(finals) - 1) / max(1, count - 1)) for position in range(count)}
    )
    errors = []
    for index in indices:
        stored = ref_from_dict(finals[index])
        task_id, output = origin[stored.key]
        task = graph.tasks[task_id]
        want = expected_prefix(
            graph,
            OutputRef(graph.id, task_id, output, task.output_types[output], task.partition),
            seed,
            cache,
        )
        data = store.read(stored)
        got = struct.unpack("<" + "f" * len(want), data[: len(want) * 4])
        for actual, expected in zip(got, want):
            error = abs(actual - expected)
            if not math.isfinite(actual) or error > 1e-6:
                raise AssertionError("independent scalar oracle mismatch")
            errors.append(error)
    return {
        "sampled_outputs": len(indices),
        "scalar_values_checked": len(errors),
        "max_abs_error": max(errors, default=0),
        "method": (
            "Python scalar float32 arithmetic, seeded PCG64 input prefixes; "
            "worker kernel not reused"
        ),
    }
