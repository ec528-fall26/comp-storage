"""Copy the clone's cephdag/ and experiments/ at HEAD into /app of every container of a lab,
then record the commit and a tree hash of what each container holds.

    python3 deploy_code.py LAB_PREFIX CLONE OUTPUT_JSON

Runs on the lab host with only Python 3 and docker. The tree hash of a directory is the
sha256 of `sha256sum`-style lines ("<file sha256>  <relative path>"), sorted by path, of
every file except __pycache__ contents; the same hash is computed for the git archive, so
equal hashes mean the container holds exactly the committed files.
"""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time

TREES = ("cephdag", "experiments")


def run(*args, data=None):
    return subprocess.run(args, input=data, stdout=subprocess.PIPE, check=True).stdout


def tree_hash(archive, top):
    """(hash, file count) of directory `top` inside tar bytes `archive`."""
    lines = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            path = Path(member.name)
            if not member.isfile() or path.parts[0] != top or "__pycache__" in path.parts:
                continue
            digest = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
            lines.append(f"{digest}  {path.as_posix()}\n")
    lines.sort(key=lambda line: line.split("  ", 1)[1])
    return hashlib.sha256("".join(lines).encode()).hexdigest(), len(lines)


def committed_archive(clone):
    """The committed trees as a tar owned by root, as /app's files are."""
    raw = run("git", "-C", clone, "archive", "--format=tar", "HEAD", *TREES)
    rewritten = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        with tarfile.open(fileobj=rewritten, mode="w") as target:
            for member in source.getmembers():
                member.uid = member.gid = 0
                member.uname = member.gname = "root"
                content = source.extractfile(member) if member.isfile() else None
                target.addfile(member, content)
    return rewritten.getvalue()


def main():
    prefix, clone, output = sys.argv[1:]
    commit = run("git", "-C", clone, "rev-parse", "HEAD").decode().strip()
    if run("git", "-C", clone, "status", "--porcelain", "--", *TREES).strip():
        raise SystemExit(f"{clone} has uncommitted changes in {TREES}")
    archive = committed_archive(clone)
    expected = {top: tree_hash(archive, top) for top in TREES}
    names = run("docker", "ps", "-a", "--filter", f"name=^{prefix}-", "--format", "{{.Names}}")
    containers = sorted(names.decode().split())
    record = {
        "commit": commit,
        "time": time.time(),
        "trees": TREES,
        "expected": {top: value[0] for top, value in expected.items()},
        "files": {top: value[1] for top, value in expected.items()},
        "containers": {},
    }
    for container in containers:
        run("docker", "cp", "-", f"{container}:/app", data=archive)
        held = {}
        for top in TREES:
            copied = run("docker", "cp", f"{container}:/app/{top}", "-")
            held[top] = tree_hash(copied, top)[0]
        record["containers"][container] = held
        print(container, held == record["expected"], flush=True)
    Path(output).write_text(json.dumps(record, indent=2) + "\n")
    mismatched = [name for name, held in record["containers"].items() if held != record["expected"]]
    if mismatched:
        raise SystemExit(f"hashes differ from the commit in {mismatched}")


if __name__ == "__main__":
    main()
