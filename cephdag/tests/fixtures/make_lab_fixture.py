"""Record the commands of the ORIGINAL three-node lab for the identity test in test_infra.py.

The original infra/lab.py, compose.json and network.py are read from the commit that
imported the refinement-v2 lab unchanged, and run against the fakes in tests/fake_host.py:

    .venv/bin/python -B tests/fixtures/make_lab_fixture.py --output tests/fixtures/lab-3node.json
"""

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.fake_host import FakeDocker, deterministic, network_commands  # noqa: E402

ORIGINAL_COMMIT = "b30483e"


def original(path):
    return subprocess.run(
        ["git", "show", f"{ORIGINAL_COMMIT}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def load_module(name, source, directory):
    path = Path(directory) / f"{name}.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(directory):
    lab = load_module("original_lab", original("infra/lab.py"), directory)
    compose = Path(directory) / "compose.json"
    compose.write_text(original("infra/compose.json"))
    state = Path(directory) / "state"
    state.mkdir()
    (state / "approval.json").write_text('{"approved": true}')
    docker = FakeDocker(state)
    patches = mock.patch.multiple(
        lab, command=docker, STATE=state, RESULTS=Path(directory) / "results", COMPOSE=compose
    )
    with patches, deterministic(), contextlib.redirect_stdout(io.StringIO()):
        lab.up()
        lab.bootstrap()
        lab.resume()
        lab.shape(400.0)
        lab.assert_owned()
        lab.docker("stop", *lab.NAMES)
    network = load_module("original_network", original("infra/network.py"), directory)
    return {
        "commit": ORIGINAL_COMMIT,
        "steps": "up, bootstrap, resume, shape 400.0, stop",
        "compose": docker.composes[0],
        "lifecycle": docker.trace,
        "network": network_commands(network, docker.composes[0]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        fixture = record(directory)
    args.output.write_text(json.dumps(fixture, indent=1) + "\n")
    print(f"{len(fixture['lifecycle'])} lifecycle commands recorded in {args.output}")


if __name__ == "__main__":
    main()
