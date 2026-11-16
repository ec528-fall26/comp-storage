"""Copy a built Rust planner library into /app/native/target/release of every container of a lab,
keeping the first container's previous library, and check what each container then holds.

    python3 deploy_native.py LAB_PREFIX LIBRARY ABI_VERSION OUTPUT_JSON

The image holds the library built at image time (ABI 5 in cephdag-lab:20.2.4-amd64-scale20261112);
deploy_code.py copies only cephdag/ and experiments/, so a commit that changes the crate also
needs this. Runs on the lab host with only Python 3 and docker. Every container must then hold a
file with the library's sha256, and every running one must load it with ABI_VERSION (a stopped one records abi null); otherwise
it exits non-zero, and the previous library, saved next to OUTPUT_JSON, can be copied back.
"""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time

TARGET = "/app/native/target/release"
NAME = "libcephdag_native.so"
LOAD = f"import ctypes; print(ctypes.CDLL('{TARGET}/{NAME}').planner_abi_version())"


def run(*args, data=None):
    return subprocess.run(args, input=data, stdout=subprocess.PIPE, check=True).stdout


def archive(content):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        member = tarfile.TarInfo(NAME)
        member.size = len(content)
        member.mode = 0o755
        member.mtime = int(time.time())
        tar.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def held(container):
    raw = run("docker", "cp", f"{container}:{TARGET}/{NAME}", "-")
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        member = tar.extractfile(tar.getmembers()[0])
        if member is None:
            raise SystemExit(f"{container}: {TARGET}/{NAME} is not a file")
        return member.read()


def running(container):
    state = run("docker", "inspect", "--format", "{{.State.Running}}", container)
    return state.decode().strip() == "true"


def main():
    prefix, library, abi, output = sys.argv[1:]
    content = Path(library).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    names = run("docker", "ps", "-a", "--filter", f"name=^{prefix}-", "--format", "{{.Names}}")
    containers = sorted(names.decode().split())
    previous = held(containers[0])
    saved = Path(output).with_name(f"{NAME}.before-{hashlib.sha256(previous).hexdigest()[:12]}")
    if not saved.exists():
        saved.write_bytes(previous)
    record = {
        "library": library,
        "sha256": digest,
        "abi": int(abi),
        "time": time.time(),
        "previous": str(saved),
        "containers": {},
    }
    for container in containers:
        run("docker", "cp", "-", f"{container}:{TARGET}", data=archive(content))
        entry = {"sha256": hashlib.sha256(held(container)).hexdigest(), "abi": None}
        if running(container):
            loaded = run("docker", "exec", container, "python3", "-c", LOAD)
            entry["abi"] = int(loaded.decode().strip())
        record["containers"][container] = entry
        print(container, entry, flush=True)
    Path(output).write_text(json.dumps(record, indent=2) + "\n")
    wrong = [
        name
        for name, entry in record["containers"].items()
        if entry["sha256"] != digest or entry["abi"] not in (None, int(abi))
    ]
    if wrong:
        raise SystemExit(f"library or ABI differs in {wrong}; the previous library is {saved}")


if __name__ == "__main__":
    main()
