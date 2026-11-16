"""Markdown table of every experiments.run row under a directory (e.g. /mnt/mass/cephdag/smoke/validate)."""
import glob
import json
import sys

print("| run | mode | policy | iterations | tasks | wall s | policy s | payload over network GB | worker NIC rx GB | worker NIC tx GB | verified | outputs sha256 |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for path in sorted(glob.glob(sys.argv[1] + "/*/rows.jsonl")):
    for line in open(path):
        r = json.loads(line)
        nic = r["network_interface_bytes"].values()
        print(
            f"| {path.split('/')[-2]} | {r['mode']} | {r['policy']} | {r['iterations'] or 'DROID'} | {r['tasks']} "
            f"| {r['wall_seconds']:.2f} | {r['policy_seconds']:.3f} | {r['network_payload_bytes']['bytes'] / 1e9:.3f} "
            f"| {sum(x['rx'] for x in nic) / 1e9:.3f} | {sum(x['tx'] for x in nic) / 1e9:.3f} "
            f"| {r['verification']['passed']} | {r['verification']['outputs_sha256'][:12]} |"
        )
