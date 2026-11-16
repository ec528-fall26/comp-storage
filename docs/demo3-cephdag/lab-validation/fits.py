import json, glob, collections, statistics, sys
base = sys.argv[1]  # e.g. /mnt/mass/cephdag/calibration/
smoke = sys.argv[2]  # e.g. /mnt/mass/cephdag/smoke/validate
for mode in ("cephdag8-cs", "cephdag8-disagg"):
    c = json.load(open(base + mode + "/calibration.json"))
    m = c["models"]
    source = sum(r["input_bytes"] for r in c["droid_samples"] if r["function"] == "decode_resize@1")
    per = collections.defaultdict(float)
    for r in c["droid_samples"]:
        per[r["function"]] += sum(s["seconds"] for s in r["result"]["stages"] if s["kind"] == "compute")
    workers = collections.Counter(r["node"] for r in c["droid_samples"])
    print(mode, "calibration_id", c["calibration_id"], "samples", len(c["samples"]), "droid samples", len(c["droid_samples"]), "on workers", dict(sorted(workers.items())))
    print("  compute fit", {k: f"{v:.4g}" for k, v in m["compute"].items() if k != "functions"}, "rmse", round(c["compute_fit_rmse_seconds"], 5))
    print("  droid source bytes", source, "CPU-s per source GB:", {f: round(v / (source / 1e9), 2) for f, v in sorted(per.items())}, "total", round(sum(per.values()) / (source / 1e9), 1))
    print("  droid fit rmse", {k: round(v, 4) for k, v in c["droid_fit_rmse_seconds"].items()})
    print("  read MB/s", [round(x / 1e6) for x in m["storage"]["read_bps"]], "sources", c["storage_sources"][:9])
    print("  write MB/s", [round(x / 1e6) for x in m["storage"]["write_bps"]])
    print("  network MB/s per NIC (from --network-mbit, not measured)", m["network"]["tx_bps"][0] / 1e6)
fit = json.load(open(base + "cephdag8-cs/models.json"))["compute"]
for path in sorted(glob.glob(smoke + "/*i[0-9]*/run-*.json")):
    r = json.load(open(path))
    it = r["case"]["iterations"]
    ratios = []
    for t in r["tasks"].values():
        size = t["result"]["outputs"][0]["size"] * 4
        predicted = fit["fixed_seconds"] + size * (fit["input_seconds_per_byte"] + it * fit["iteration_seconds_per_byte"]) + size / 4 * fit["output_seconds_per_byte"]
        actual = sum(s["seconds"] for s in t["result"]["stages"] if s["kind"] == "compute")
        ratios.append(actual / predicted)
    print(path.split(smoke)[1], "compute actual/model median", round(statistics.median(ratios), 2), "range", round(min(ratios), 2), "-", round(max(ratios), 2))
