"""Functions a job can name.

Each entry maps a function name to ``module:callable``. The callable is
``run(job, root, scratch) -> (local_output_dir, info)``: it reads the job's
inputs from ``root`` (CephFS), writes its result under ``scratch`` (local
disk), and the worker then publishes that directory to ``root / job.output``.
"""

from __future__ import annotations

import importlib
from typing import Callable

REGISTRY = {
    "droid_rlds_to_lerobot": "cstore.functions.droid_rlds_to_lerobot:run",
}


def resolve(name: str) -> Callable:
    try:
        target = REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown function {name!r}; known: {', '.join(sorted(REGISTRY))}") from None
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)
