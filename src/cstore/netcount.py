"""Network byte counters from /proc/net/dev.

Cross-host traffic is measured as the change in a host NIC's receive and
transmit byte counters across a job. With one storage node, every byte that
crosses a host boundary in our setup enters or leaves the storage node's NIC,
so its counters count each transfer once. Loopback traffic (a worker on the
storage node talking to the OSDs on the same host) never touches the NIC.
"""

from __future__ import annotations

import time
from pathlib import Path

PROC_NET_DEV = Path("/proc/net/dev")
PROC_NET_ROUTE = Path("/proc/net/route")


def parse_net_dev(text: str) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        fields = [int(x) for x in rest.split()]
        if len(fields) < 16:
            continue
        stats[name.strip()] = {
            "rx_bytes": fields[0],
            "rx_packets": fields[1],
            "tx_bytes": fields[8],
            "tx_packets": fields[9],
        }
    return stats


def default_route_iface(route_text: str | None = None) -> str | None:
    text = route_text if route_text is not None else PROC_NET_ROUTE.read_text()
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 4 and cols[1] == "00000000" and int(cols[3], 16) & 0x1:
            return cols[0]
    return None


def snapshot(iface: str = "") -> dict:
    stats = parse_net_dev(PROC_NET_DEV.read_text())
    name = iface or default_route_iface() or next((n for n in stats if n != "lo"), "lo")
    if name not in stats:
        raise ValueError(f"interface {name!r} not found in /proc/net/dev (have: {', '.join(stats)})")
    return {"time": time.time(), "iface": name, **stats[name]}


def delta(before: dict, after: dict) -> dict:
    if before["iface"] != after["iface"]:
        raise ValueError("snapshots are from different interfaces")
    rx = after["rx_bytes"] - before["rx_bytes"]
    tx = after["tx_bytes"] - before["tx_bytes"]
    return {
        "iface": after["iface"],
        "seconds": round(after["time"] - before["time"], 3),
        "rx_bytes": rx,
        "tx_bytes": tx,
        "total_bytes": rx + tx,
        "rx_packets": after["rx_packets"] - before["rx_packets"],
        "tx_packets": after["tx_packets"] - before["tx_packets"],
    }
