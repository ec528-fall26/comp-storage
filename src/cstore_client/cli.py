"""Command line for the job API.

    cstore-client options                         what can be selected
    cstore-client submit --tasks put --objects cup --drop-idle --wait
    cstore-client submit --config request.json    from a saved request
    cstore-client submit -i                       answer questions instead of flags
    cstore-client status <job_id>
    cstore-client download <job_id> --dest ./slice
    cstore-client init [request.json]             write a request template

Every flag is optional. --server picks where requests go: "mock" (default,
no network, prints what it would send) or http://<storage-node-ip>:8000.
--json prints machine-readable output for scripts and agents.

Exit codes: 0 success, 1 job failed or server unreachable, 2 bad request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from . import client
from .request import CAMERAS, DEFAULT_MAX_EPISODES, RequestError, SliceRequest
from .transport import TransportError, make_transport

EXIT_OK, EXIT_FAILED, EXIT_BAD_REQUEST = 0, 1, 2


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _mb(n: int | None) -> str:
    return "-" if n is None else f"{n / 1e6:,.1f} MB"


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cstore-client",
        description="Request a custom slice of DROID, built on the storage nodes where the data lives.",
    )
    p.add_argument("--server", default=None,
                   help="'mock' (default; no network) or http://<ip>:8000. Env: CSTORE_SERVER")
    p.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("submit", help="submit a slice request")
    g = s.add_argument_group("select episodes (keywords match the language instruction)")
    g.add_argument("--tasks", help="comma-separated task words, e.g. put,pick (default: any)")
    g.add_argument("--objects", help="comma-separated object words, e.g. cup,mug (default: any)")
    g.add_argument("--labs", help="comma-separated lab names (default: any)")
    g.add_argument("--episode-ids", help="comma-separated episode ids (default: any)")
    g.add_argument("--max-episodes", type=int,
                   help=f"cap on matched episodes (default {DEFAULT_MAX_EPISODES}; 0 = no cap)")
    g = s.add_argument_group("trim inside each episode")
    g.add_argument("--drop-idle", action="store_const", const=True, default=None,
                   help="remove stretches where the arm is not moving")
    g.add_argument("--cameras", help=f"comma-separated, from {','.join(CAMERAS)} (default: all)")
    g.add_argument("--frame-stride", type=int, help="keep every Nth step; 15 = 1 frame/s (default 1)")
    g = s.add_argument_group("how to submit")
    g.add_argument("--config", help="start from a saved request JSON; flags override its values")
    g.add_argument("-i", "--interactive", action="store_true", help="choose options from menus")
    g.add_argument("--save", help="also write the final request to this JSON file")
    g.add_argument("--dry-run", action="store_true", help="validate and print the request; don't send")
    g.add_argument("--wait", action="store_true", help="wait for the job to finish")
    g.add_argument("--download", metavar="DIR", help="wait, then download the result into DIR")

    st = sub.add_parser("status", help="show a job's status")
    st.add_argument("job_id")

    d = sub.add_parser("download", help="download a finished job's result")
    d.add_argument("job_id")
    d.add_argument("--dest", default=".", help="folder to download into (default: current)")

    sub.add_parser("options", help="list the values you can select from")

    i = sub.add_parser("init", help="write a request template to edit")
    i.add_argument("path", nargs="?", default="request.json")
    return p


def request_from_args(args: argparse.Namespace) -> SliceRequest:
    base = SliceRequest.load(args.config).to_dict() if args.config else SliceRequest().to_dict()
    sel, trim = base["select"], base["trim"]
    if args.tasks is not None:
        sel["task_keywords"] = args.tasks
    if args.objects is not None:
        sel["object_keywords"] = args.objects
    if args.labs is not None:
        sel["labs"] = args.labs.split(",")
    if args.episode_ids is not None:
        sel["episode_ids"] = args.episode_ids.split(",")
    if args.max_episodes is not None:
        sel["max_episodes"] = None if args.max_episodes == 0 else args.max_episodes
    if args.drop_idle is not None:
        trim["drop_idle"] = True
    if args.cameras is not None:
        trim["cameras"] = args.cameras
    if args.frame_stride is not None:
        trim["frame_stride"] = args.frame_stride
    return SliceRequest.from_dict(base)


# ---------------------------------------------------------------------------
# interactive mode (standard library prompts, no extra packages)
# ---------------------------------------------------------------------------

def _pick_many(ask: Callable[[str], str], out: Callable[[str], None], title: str,
               choices: list[str], default: list[str]) -> list[str]:
    out(f"\n{title}")
    for n, c in enumerate(choices, 1):
        mark = "*" if c in default else " "
        out(f"  {n:2d}. [{mark}] {c}")
    shown = ",".join(default) if default else "any"
    while True:
        raw = ask(f"  numbers separated by commas, Enter = {shown}: ").strip()
        if not raw:
            return list(default)
        try:
            picks = [choices[int(x) - 1] for x in raw.replace(" ", "").split(",") if x]
            if any(int(x) < 1 for x in raw.replace(" ", "").split(",") if x):
                raise IndexError
            return list(dict.fromkeys(picks))
        except (ValueError, IndexError):
            out(f"  enter numbers between 1 and {len(choices)}")


def _ask_bool(ask, out, prompt: str, default: bool) -> bool:
    while True:
        raw = ask(f"\n{prompt} ({'Y/n' if default else 'y/N'}): ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        out("  answer y or n")


def _ask_int(ask, out, prompt: str, default: int | None, minimum: int) -> int | None:
    while True:
        raw = ask(f"\n{prompt} (Enter = {default if default is not None else 'no cap'}): ").strip()
        if not raw:
            return default
        try:
            v = int(raw)
            if v >= minimum:
                return v
        except ValueError:
            pass
        out(f"  enter a whole number >= {minimum}")


def interactive_request(opts: dict[str, list[str]], start: SliceRequest,
                        ask: Callable[[str], str] = input,
                        out: Callable[[str], None] = _stderr) -> SliceRequest:
    s, t = start.select, start.trim
    out("Build a slice request. Press Enter to keep the value shown.")
    tasks = _pick_many(ask, out, "Task types (any one matches; none selected = any):",
                       opts.get("tasks", []), s.task_keywords)
    objects = _pick_many(ask, out, "Object types (any one matches; none selected = any):",
                         opts.get("objects", []), s.object_keywords)
    labs = _pick_many(ask, out, "Labs (none selected = any):", opts.get("labs", []), s.labs)
    max_eps = _ask_int(ask, out, "Maximum episodes", s.max_episodes, 1)
    drop_idle = _ask_bool(ask, out, "Drop idle frames (arm not moving)?", t.drop_idle)
    cameras = _pick_many(ask, out, "Cameras to keep:", list(CAMERAS), t.cameras)
    stride = _ask_int(ask, out, "Keep every Nth frame (1 = all, 15 = 1 per second)", t.frame_stride, 1)
    return SliceRequest.build(
        tasks=tasks, objects=objects, labs=labs, episode_ids=s.episode_ids,
        max_episodes=max_eps, drop_idle=drop_idle, cameras=cameras or list(CAMERAS),
        frame_stride=stride,
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _print_status(st: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(st))
        return
    print(f"{st['job_id']}: {st['status']}")
    for key, label in (("episodes_matched", "episodes matched"), ("shards_touched", "shards touched")):
        if st.get(key) is not None:
            print(f"  {label}: {st[key]}")
    if st.get("nodes"):
        print(f"  ran on: {', '.join(st['nodes'])}")
    if "bytes_out" in st:
        print(f"  bytes returned: {_mb(st.get('bytes_out'))}")
    if st.get("bytes_baseline") is not None:
        print(f"  baseline (pull whole shards): {_mb(st['bytes_baseline'])}")


def cmd_submit(args, transport) -> int:
    req = request_from_args(args)
    if args.interactive:
        if not sys.stdin.isatty():
            raise RequestError("-i needs an interactive terminal; use flags or --config instead")
        req = interactive_request(transport.options(), req)
        if not args.save and _ask_bool(input, _stderr, "Save these choices to request.json?", True):
            args.save = "request.json"
    if args.save:
        req.save(args.save)
        if not args.json:
            _stderr(f"saved request to {args.save}")
    if args.dry_run:
        print(req.to_json())
        return EXIT_OK

    if not args.json:
        _stderr(f"submitting: {req.summary()}")
    job = client.submit(req, transport=transport)
    if not (args.wait or args.download):
        if args.json:
            print(json.dumps({"job_id": job.job_id, "status": "submitted"}))
        else:
            print(f"submitted {job.job_id}")
            print(f"  check:    cstore-client status {job.job_id}")
            print(f"  download: cstore-client download {job.job_id} --dest ./slice")
        return EXIT_OK

    on_update = None if args.json else (lambda st: _stderr(f"  {job.job_id}: {st['status']}"))
    final = job.wait(poll=0.5, on_update=on_update)
    result: dict[str, Any] = dict(final)
    if final["status"] == "done" and args.download:
        result["files"] = [str(p) for p in job.download(args.download)]
    if args.json:
        print(json.dumps(result))
    else:
        _print_status(final, False)
        for f in result.get("files", []):
            print(f"  downloaded: {f}")
    return EXIT_OK if final["status"] == "done" else EXIT_FAILED


def cmd_status(args, transport) -> int:
    st = transport.status(args.job_id)
    _print_status(st, args.json)
    return EXIT_FAILED if st["status"] == "failed" else EXIT_OK


def cmd_download(args, transport) -> int:
    files = client.job(args.job_id, transport=transport).download(args.dest)
    if args.json:
        print(json.dumps({"job_id": args.job_id, "files": [str(f) for f in files]}))
    else:
        for f in files:
            print(f"downloaded: {f}")
    return EXIT_OK


def cmd_options(args, transport) -> int:
    opts = transport.options()
    if args.json:
        print(json.dumps(opts))
    else:
        for key, values in opts.items():
            print(f"{key}: {', '.join(values)}")
    return EXIT_OK


def cmd_init(args, transport) -> int:
    path = Path(args.path)
    if path.exists():
        raise RequestError(f"{path} already exists; pick another name")
    example = SliceRequest.build(tasks=["put"], objects=["cup"], drop_idle=True,
                                 cameras=["wrist", "exterior_1"], frame_stride=15)
    example.save(path)
    if args.json:
        print(json.dumps({"written": str(path)}))
    else:
        print(f"wrote {path}; edit it, then: cstore-client submit --config {path}")
    return EXIT_OK


COMMANDS = {"submit": cmd_submit, "status": cmd_status, "download": cmd_download,
            "options": cmd_options, "init": cmd_init}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        transport = make_transport(args.server, log=None if args.json else _stderr)
        return COMMANDS[args.command](args, transport)
    except RequestError as e:
        _stderr(f"bad request: {e}")
        return EXIT_BAD_REQUEST
    except TransportError as e:
        _stderr(f"error: {e}")
        return EXIT_FAILED
    except KeyboardInterrupt:
        _stderr("cancelled")
        return EXIT_FAILED
