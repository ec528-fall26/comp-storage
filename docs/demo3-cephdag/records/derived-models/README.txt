Models derived from the cephdag8 calibrations without recalibrating (2026-11-13, aakashpc,
cephdag demo3 bb7de4b, checkout ~/experiments/cephdag-demo3-next, host python3 3.14.10;
the same commands under Python 3.9.25 on JarvisVM gave byte-identical files):

  python3 -m experiments.derive_models /mnt/mass/cephdag/calibration/cephdag8-disagg/models.json /mnt/mass/cephdag/derived-models/cephdag8-disagg2/models.json --slots 2 --storage-nodes 8 --fused
  python3 -m experiments.derive_models /mnt/mass/cephdag/calibration/cephdag8-cs/models.json /mnt/mass/cephdag/derived-models/cephdag8-cs-fused/models.json --fused
  python3 -m experiments.derive_models /mnt/mass/cephdag/calibration/cephdag8-disagg/models.json /mnt/mass/cephdag/derived-models/cephdag8-disagg-fused/models.json --fused

disagg2: 24 nodes, each second compute worker repeats its container's rates (fifo-hash only).
fused: droid_fused@1 costs 49.8 s per source GB. SHA256SUMS lists the outputs and the two
sources; demo3-followon.sh checks it before the campaigns run.
