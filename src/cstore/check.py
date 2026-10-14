"""Check that the two-node setup is what the experiments assume.

Run on the compute node: ``bin/cstore check``. Each line is one check.
"""

from __future__ import annotations

import subprocess
import uuid

from . import hosts, netcount
from .config import Config
from .runner import fs_type


def _route_iface(ip: str) -> str | None:
    out = subprocess.run(["ip", "-o", "route", "get", ip], capture_output=True, text=True).stdout.split()
    return out[out.index("dev") + 1] if "dev" in out else None


def _image_id(image: str, remote_cfg: Config | None = None) -> str:
    cmd = f"docker image inspect --format '{{{{.Id}}}}' {image}"
    if remote_cfg is None:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
    return hosts.ssh(remote_cfg, cmd, check=False).stdout.strip()


def run_checks(cfg: Config, log=print) -> bool:
    ok_all = True

    def report(ok: bool, what: str, detail: str = "", warn_only: bool = False) -> None:
        nonlocal ok_all
        if not ok and not warn_only:
            ok_all = False
        label = "OK  " if ok else ("WARN" if warn_only else "FAIL")
        log(f"{label} {what}" + (f": {detail}" if detail else ""))

    report(cfg.role == "compute", "this is the compute node", f"CSTORE_ROLE={cfg.role or '(unset)'}")

    local_fs = fs_type(cfg.root) or ""
    # kernel client: "ceph"; ceph-fuse: "fuse.ceph-fuse" (or "fuse.ceph")
    report(local_fs == "ceph" or local_fs.startswith("fuse.ceph"), f"{cfg.root} is on CephFS here", f"type {local_fs}")

    try:
        remote_name = hosts.ssh(cfg, "hostname").stdout.strip()
        report(True, "ssh to the storage node", f"{cfg.storage_host} ({remote_name})")
    except Exception as exc:  # noqa: BLE001
        report(False, "ssh to the storage node", str(exc))
        return False

    remote_fs = hosts.ssh(cfg, f"findmnt -no FSTYPE --target {cfg.root}", check=False).stdout.strip()
    report(remote_fs == local_fs, "storage node mounts the same way", f"type {remote_fs}")

    token = uuid.uuid4().hex
    probe = cfg.root / ".check" / f"from-compute-{token}"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(token)
    seen = hosts.ssh(cfg, f"cat {probe}", check=False).stdout.strip()
    report(seen == token, "a file written here is read back on the storage node")
    back = cfg.root / ".check" / f"from-storage-{token}"
    hosts.ssh(cfg, f"echo {token} > {back}", check=False)
    report(back.exists() and back.read_text().strip() == token, "a file written on the storage node is read here")
    probe.unlink(missing_ok=True)
    back.unlink(missing_ok=True)

    health = hosts.ssh(cfg, "sudo -n microceph.ceph health", check=False)
    text = (health.stdout or health.stderr).strip()
    # HEALTH_WARN is reported but not fatal (e.g. placement groups still
    # settling right after setup); HEALTH_ERR or no answer fails the check.
    report(text.startswith("HEALTH_OK"), "Ceph health", text or "no answer", warn_only=text.startswith("HEALTH_WARN"))

    storage_ip = cfg.storage_host.split("@")[-1]
    iface = netcount.snapshot(cfg.net_iface)["iface"]
    route = _route_iface(storage_ip)
    report(route == iface, "traffic to the storage node uses the counted interface", f"{route} (counting {iface})")
    remote_snap = hosts.last_json_line(hosts.storage_cstore(cfg, ["netsnap"]).stdout)
    report(True, "storage node interface counted", remote_snap["iface"])

    local_img, remote_img = _image_id(cfg.worker_image), _image_id(cfg.worker_image, cfg)
    report(
        bool(local_img) and local_img == remote_img,
        f"identical worker image {cfg.worker_image} on both nodes",
        local_img[:19] if local_img == remote_img else f"here {local_img[:19] or 'missing'}, storage {remote_img[:19] or 'missing'}",
    )

    remote_cfg = hosts.last_json_line(hosts.storage_cstore(cfg, ["config"]).stdout)
    same_limits = (remote_cfg["worker_cpus"], remote_cfg["worker_memory"]) == (cfg.worker_cpus, cfg.worker_memory)
    report(
        same_limits,
        "same worker CPU and memory limits on both nodes",
        f"here {cfg.worker_cpus} CPUs / {cfg.worker_memory}, storage {remote_cfg['worker_cpus']} / {remote_cfg['worker_memory']}",
    )

    unit = hosts.ssh(cfg, "systemctl cat cstore-service >/dev/null && systemctl is-active cstore-service", check=False)
    report(unit.returncode in (0, 3), "background service unit installed on the storage node", unit.stdout.strip())

    log("all checks passed" if ok_all else "some checks FAILED")
    return ok_all
