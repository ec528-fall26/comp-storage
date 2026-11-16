"""The function registry: every function is declared once, with its implementation.

Clients, the coordinator and every worker import the same modules, so a graph can
only name functions that the workers can run. Code is never sent over the network.
"""

from dataclasses import dataclass
import importlib
import re

import numpy as np

NAME = re.compile(r"[A-Za-z0-9_.-]+")
CHUNK_VALUES = 2**18


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    version: str
    input_types: tuple
    output_types: tuple

    @property
    def key(self):
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class Function:
    spec: FunctionSpec
    run: object


REGISTRY = {}


def function(name, version, *, inputs, outputs):
    """Register `implementation(inputs, params, output_sizes) -> tuple of bytes`.

    Type tags in `inputs` and `outputs` are labels that a graph checks for agreement.
    `output_sizes` are the planner's estimates; the function may return other sizes.
    """
    spec = FunctionSpec(name, str(version), tuple(inputs), tuple(outputs))
    if not NAME.fullmatch(spec.name) or not NAME.fullmatch(spec.version):
        raise ValueError(f"invalid function name or version: {spec.key}")

    def register(implementation):
        if spec.key in REGISTRY:
            raise ValueError(f"function already registered: {spec.key}")
        REGISTRY[spec.key] = Function(spec, implementation)
        return implementation

    return register


def lookup(key):
    if key not in REGISTRY:
        raise ValueError(f"unregistered function: {key}")
    return REGISTRY[key]


def import_modules(names):
    for name in names:
        importlib.import_module(name)


def numeric(inputs, params, output_sizes):
    """Average the inputs, iterate a float32 map, and resize to each requested output."""
    arrays = [np.frombuffer(data, dtype="<f4") for data in inputs]
    length = max((len(array) for array in arrays), default=0)
    result = np.zeros(length, dtype="<f4")
    for array in arrays:
        if len(array) == length:
            result += array
        elif len(array):
            result += np.resize(array, length)
    if arrays:
        result /= len(arrays)
    # The map is elementwise, so iterating one cache-sized chunk at a time gives the same
    # bytes; over a whole 64 MiB array, workers on one host contend for its memory bus.
    for start in range(0, length, CHUNK_VALUES):
        chunk = result[start : start + CHUNK_VALUES]
        for _ in range(params["iterations"]):
            np.multiply(chunk, np.float32(1.0001), out=chunk)
            np.add(chunk, np.float32(0.0001), out=chunk)
            np.tanh(chunk, out=chunk)
    outputs = []
    for size in output_sizes:
        if not size:
            outputs.append(b"")
        elif not length:
            raise ValueError("cannot produce nonempty output from empty numerical input")
        else:
            outputs.append(np.resize(result, size // 4).astype("<f4", copy=False).tobytes())
    return tuple(outputs)


function("map", "1", inputs=["float32"], outputs=["float32"])(numeric)
function("join", "1", inputs=["float32", "float32"], outputs=["float32"])(numeric)
function("split", "1", inputs=["float32"], outputs=["float32", "float32"])(numeric)


def synthetic(size, seed):
    """Reproducible float32 test data; `size` is in bytes."""
    if size < 0 or size % 4:
        raise ValueError("float32 shard size must be a nonnegative multiple of four")
    values = np.random.default_rng(seed).random(size // 4, dtype=np.float32)
    return values.astype("<f4", copy=False).tobytes()
