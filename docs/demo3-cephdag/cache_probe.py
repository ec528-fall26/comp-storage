"""Seconds of the map@1 compute on N containers at once, per input size: where is the cache cliff?

    python3 cache_probe.py CONTAINER [CONTAINER ...]

Runs cephdag.functions.numeric in every container at the same time, at 32 iterations and
output ratio 0.25, three times per size, and prints the median seconds and the seconds per
MiB and iteration. Run on the lab host.
"""

from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
import statistics
import subprocess
import sys

PROBE = """
import sys, time
sys.path.insert(0, "/app")
from cephdag.functions import numeric, synthetic
size = int(sys.argv[1]) * 2**20
data = synthetic(size, 7)
began = time.perf_counter()
numeric([data], {"iterations": int(sys.argv[2])}, [size // 4])
print(time.perf_counter() - began)
"""
SIZES_MIB = (8, 16, 24, 32, 48, 64)
ITERATIONS = 32


def seconds(container, mib):
    argv = ["docker", "exec", container, "python3", "-c", PROBE, str(mib), str(ITERATIONS)]
    return float(subprocess.run(argv, stdout=subprocess.PIPE, check=True).stdout)


def main():
    containers = sys.argv[1:]
    with ThreadPoolExecutor(max_workers=len(containers)) as executor:
        for mib in SIZES_MIB:
            times = []
            for _ in range(3):
                times += executor.map(partial(seconds, mib=mib), containers)
            median = statistics.median(times)
            row = {
                "mib": mib,
                "containers": len(containers),
                "median_s": round(median, 4),
                "min_s": round(min(times), 4),
                "max_s": round(max(times), 4),
                "us_per_mib_iteration": round(median / mib / ITERATIONS * 1e6, 1),
            }
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
