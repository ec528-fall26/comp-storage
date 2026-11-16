"""Fixed-window shared-NIC validation, independent of iperf end-summary delay.

Run through `python3 -m infra.lab verify --lab <config> --probe boundary-window`.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import time

from infra.lab import assert_owned, inside, ring_flows, save, start_iperf_servers


def counter(lab, node, direction):
    script = (
        "import json,time; p='/sys/class/net/eth0/statistics/" + direction + "_bytes'; "
        "t=time.monotonic(); b=int(open(p).read()); print(json.dumps({'time':(t+time.monotonic())/2,'bytes':b}))"
    )
    return json.loads(inside(lab, node, "python3", "-c", script))


def measure(lab, mode, mbit):
    """Each node of `mode` in turn: its eth0 byte counters over 10 s while two flows leave it, then arrive."""
    assert_owned(lab)
    ring = lab.active(mode)
    rows = {}
    rates = {}
    try:
        start_iperf_servers(lab, ring)
        with ThreadPoolExecutor(max_workers=2) as executor:
            for node in ring:
                rates[node] = {}
                for name, flows in ring_flows(ring, node).items():
                    direction = "tx" if name == "shared_tx" else "rx"
                    futures = [
                        executor.submit(inside, lab, source, *client(lab, destination, port))
                        for source, destination, port in flows
                    ]
                    time.sleep(2)
                    before = counter(lab, node, direction)
                    time.sleep(10)
                    after = counter(lab, node, direction)
                    rate = (
                        (after["bytes"] - before["bytes"])
                        * 8
                        / (after["time"] - before["time"])
                        / 1e6
                    )
                    clients = [json.loads(future.result()) for future in futures]
                    rows[f"n{node}-{name}"] = dict(
                        direction=direction,
                        before=before,
                        after=after,
                        boundary_mbit=rate,
                        clients=clients,
                    )
                    rates[node][name] = rate
                    save(lab, f"boundary-window-{mode}-{mbit:g}.json", rows)
                    print(
                        json.dumps({"node": node, "direction": name, "boundary_mbit": rate}),
                        flush=True,
                    )
    finally:
        for node in ring:
            inside(lab, node, "pkill", "-x", "iperf3")
    return rates


def client(lab, destination, port):
    return ["iperf3", "-c", lab.address(destination), "-p", str(port), "-t", "20", "-O", "1", "-J"]
