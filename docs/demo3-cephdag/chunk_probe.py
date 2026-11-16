"""Would a cache-sized loop make the synthetic compute scale with workers? A probe.

    python3 chunk_probe.py CONTAINER [CONTAINER ...]

In every container at once, iterates the map@1 update (multiply, add, tanh, in place) 32
times over a 64 MiB float32 array, either over the whole array per iteration (as
cephdag.functions.numeric does) or chunk by chunk, all iterations on one chunk before the
next. The update is elementwise, so both give the same bytes; the probe checks that. Prints
median seconds per container count and loop. Run on the lab host while the lab is idle.
"""

from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
import statistics
import subprocess
import sys

PROBE = """
import hashlib, sys, time
import numpy as np
sys.path.insert(0, "/app")
from cephdag.functions import synthetic
size, iterations, chunk = (int(value) for value in sys.argv[1:])
result = np.frombuffer(synthetic(size, 7), dtype="<f4").copy()
began = time.perf_counter()
step = chunk // 4 if chunk else len(result)
for start in range(0, len(result), step):
    part = result[start:start + step]
    for _ in range(iterations):
        np.multiply(part, np.float32(1.0001), out=part)
        np.add(part, np.float32(0.0001), out=part)
        np.tanh(part, out=part)
print(time.perf_counter() - began, hashlib.sha256(result.tobytes()).hexdigest()[:12])
"""
MIB = 2**20


def seconds(container, chunk):
    argv = ["docker", "exec", container, "python3", "-c", PROBE, str(64 * MIB), "32", str(chunk)]
    elapsed, digest = subprocess.run(argv, stdout=subprocess.PIPE, check=True).stdout.split()
    return float(elapsed), digest.decode()


def main():
    containers = sys.argv[1:]
    for count in (1, len(containers)):
        for chunk in (0, MIB):
            with ThreadPoolExecutor(max_workers=count) as executor:
                results = []
                for _ in range(3):
                    results += executor.map(partial(seconds, chunk=chunk), containers[:count])
            times = [elapsed for elapsed, _ in results]
            row = {
                "containers": count,
                "chunk_mib": chunk / MIB or "whole",
                "median_s": round(statistics.median(times), 4),
                "max_s": round(max(times), 4),
                "digests": sorted({digest for _, digest in results}),
            }
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
