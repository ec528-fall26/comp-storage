"""Private routed overlay; changes only this container's network namespace.

Docker internal bridge filtering drops non-subnet destinations even when the
next-hop MAC belongs to a router on that bridge. IPIP keeps each underlay packet
within its private bridge. Source policy tables keep router tunnel endpoints on
the underlay while inner node packets take the tunnel. No host rules are changed.

Node n owns the n-th subnet of SUBNETS: it is .10 there and the router is .254.
"""

import ipaddress
import json
import os
import subprocess

TBF_LIMITS = ("burst", "128kb", "latency", "400ms")


def run(*args):
    subprocess.run(args, check=True)


def query(*args):
    return json.loads(subprocess.check_output(["ip", "-j", *args]))


def interface_with(local, addresses):
    for interface in addresses:
        if any(a.get("local") == local for a in interface["addr_info"]):
            return interface["ifname"]
    raise RuntimeError(f"no interface has address {local}")


def add_tunnel(name, local, remote, dev):
    if not any(x["ifname"] == name for x in query("link")):
        ends = ["local", local, "remote", remote, "dev", dev]
        run("ip", "tunnel", "add", name, "mode", "ipip", *ends)


def shape(dev, rate):
    run("tc", "qdisc", "replace", "dev", dev, "root", "tbf", "rate", rate + "mbit", *TBF_LIMITS)


def main():
    rate = os.environ.get("BANDWIDTH_MBIT", "100")
    role = os.environ["ROLE"]
    subnets = [ipaddress.ip_network(subnet) for subnet in os.environ["SUBNETS"].split(",")]
    addresses = query("addr")
    if role == "router":
        for n, subnet in enumerate(subnets):
            local = str(subnet.network_address + 254)
            node = str(subnet.network_address + 10)
            dev = interface_with(local, addresses)
            table = str(100 + n)
            tunnel = f"lab{n}"
            # Endpoint lookup must remain on the original connected link.
            run("ip", "route", "replace", "table", table, str(subnet), "dev", dev, "src", local)
            if not any(x.get("priority") == 100 + n for x in query("rule")):
                run("ip", "rule", "add", "priority", table, "from", local + "/32", "lookup", table)
            add_tunnel(tunnel, local, node, dev)
            run("ip", "addr", "replace", f"10.77.{n}.2/30", "dev", tunnel)
            run("ip", "link", "set", tunnel, "up", "mtu", "1480")
            run("ip", "route", "replace", f"{node}/32", "via", f"10.77.{n}.1", "dev", tunnel)
            shape(dev, rate)
    else:
        n = int(os.environ["NODE"])
        local = str(subnets[n].network_address + 10)
        router = str(subnets[n].network_address + 254)
        gateway = f"10.77.{n}.2"
        add_tunnel("lab", local, router, "eth0")
        run("ip", "addr", "replace", f"10.77.{n}.1/30", "dev", "lab")
        run("ip", "link", "set", "lab", "up", "mtu", "1480")
        for other, subnet in enumerate(subnets):
            if other != n:
                run(
                    "ip",
                    "route",
                    "replace",
                    str(subnet),
                    "via",
                    gateway,
                    "dev",
                    "lab",
                    "src",
                    local,
                )
        shape("eth0", rate)


if __name__ == "__main__":
    main()
