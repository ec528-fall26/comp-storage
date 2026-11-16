import json, sys, glob, collections
for path in sorted(glob.glob(sys.argv[1] + "/**/rows.jsonl", recursive=True)):
    for line in open(path):
        r = json.loads(line)
        p = r["network_payload_bytes"]
        tasks_by_worker = None
        print(f"{path.split('/results/')[-1] if '/results/' in path else path} {r['mode']} {r['policy']:<10} it={r['iterations']} tasks={r['tasks']} wall={r['wall_seconds']:.2f}s policy={r['policy_seconds']:.3f}s payload={p['bytes']/1e6:.1f}MB iface_tx={sum(v['tx'] for v in r['network_interface_bytes'].values())/1e6:.1f}MB pass={r['verification']['passed']} digest={r['verification']['outputs_sha256'][:12]}")
