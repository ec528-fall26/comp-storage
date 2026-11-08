"""HTTP job API for slice requests (Demo 3).

    uvicorn cstore.api:app --host 0.0.0.0 --port 8000

Runs on a storage node. It never runs user code or touches the data itself:
``POST /jobs`` validates the request and writes a job file into the CephFS
queue (``cstore.jobqueue``); the background service (``cstore service``)
claims it and runs ``droid_rlds_slice`` in a worker. Status is read back from
where the job file sits (pending/running/done/failed) and its result record.

Endpoints (what ``cstore_client.HttpTransport`` calls):

    POST /jobs                {select, trim, output}  -> {job_id, status}
    GET  /jobs/<id>           status, counts, bytes
    GET  /jobs/<id>/result    the slice's manifest.json (the slice itself stays on CephFS)
    GET  /options             values a request can use
    GET  /health
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from cstore_client.request import CAMERAS, RequestError, SliceRequest

from . import config, jobqueue
from .jobs import Job
from .service import shard_indices

SLICE_FUNCTION = "droid_rlds_slice"
DATASET_DIR = "datasets/droid/rlds/1.0.1"
STATUS_NAMES = {"pending": "queued", "running": "running", "done": "done", "failed": "failed"}

# Vocabulary for GET /options. Static for v0; later it comes from an index of
# what is actually stored (labs, instruction words).
TASK_WORDS = ["put", "move", "pick", "remove", "take", "open", "close", "place", "turn", "pour", "wipe"]
OBJECT_WORDS = ["marker", "cloth", "cup", "bottle", "block", "drawer", "lid", "mug", "bowl", "towel", "pen"]
LABS = ["AUTOLab", "CLVR", "GuptaLab", "ILIAD", "IPRL", "IRIS", "PennPAL", "RAD", "RAIL", "REAL", "RPL", "TRI", "WEIRD"]

cfg = config.load()
app = FastAPI(title="cstore job API", version="0.1")
TOKEN = os.environ.get("CSTORE_TOKEN")


@app.middleware("http")
async def _auth(request: Request, call_next):
    if TOKEN and request.url.path != "/health":
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "missing or wrong bearer token"}, status_code=401)
    return await call_next(request)


def _find(job_id: str) -> tuple[str, dict[str, Any]]:
    for state, entry in jobqueue.entries(cfg):
        if entry["job"]["job_id"] == job_id:
            return state, entry
    raise HTTPException(404, f"no job {job_id!r}")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "root": str(cfg.root), "shards_loaded": shard_indices(cfg)}


@app.get("/options")
def options() -> dict[str, Any]:
    # Lists of strings only: the client prints and offers these as menu choices.
    return {"tasks": TASK_WORDS, "objects": OBJECT_WORDS, "labs": LABS, "cameras": list(CAMERAS)}


@app.post("/jobs", status_code=202)
def submit(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        req = SliceRequest.from_dict(body)
    except RequestError as e:
        raise HTTPException(400, str(e)) from None
    shards = shard_indices(cfg)
    if not shards:
        raise HTTPException(503, f"no DROID shards loaded under {cfg.root / DATASET_DIR}")
    job_id = f"slice-{uuid.uuid4().hex[:8]}"
    job = Job(
        job_id=job_id,
        function=SLICE_FUNCTION,
        # Declared dependencies: placement (multi-node, later) uses these.
        inputs=[f"{DATASET_DIR}/dataset_info.json", f"{DATASET_DIR}/features.json"]
        + [f"{DATASET_DIR}/droid_101-train.tfrecord-{i:05d}-of-02048" for i in shards],
        output=f"outputs/slices/{job_id}",
        params={"request": req.to_dict(), "dataset_dir": DATASET_DIR},
    )
    jobqueue.submit(cfg, job)
    return {"job_id": job_id, "status": "queued", "shards": len(shards)}


@app.get("/jobs/{job_id}")
def status(job_id: str) -> dict[str, Any]:
    state, entry = _find(job_id)
    out: dict[str, Any] = {"job_id": job_id, "status": STATUS_NAMES[state],
                           "shards_touched": len([p for p in entry["job"]["inputs"] if ".tfrecord-" in p]),
                           "nodes": [entry["state"]["claimed_by"]] if entry["state"].get("claimed_by") else []}
    result_path = entry["state"].get("result")
    if result_path and Path(result_path).exists():
        record = json.loads(Path(result_path).read_text())
        w = record.get("worker") or {}
        out.update({
            "episodes_scanned": w.get("episodes_scanned"),
            "episodes_matched": w.get("episodes_kept"),
            "shards_touched": w.get("shards_read", out["shards_touched"]),
            "bytes_out": w.get("bytes_out"),
            "bytes_baseline": w.get("bytes_scanned"),
            "output": entry["job"]["output"],
            "warnings": w.get("warnings", []),
            "wall_s": record.get("wall_s"),
        })
        if not record.get("ok"):
            out["error"] = w.get("error") or "worker failed; see log " + str(record.get("log"))
    return out


@app.get("/jobs/{job_id}/result")
def result(job_id: str):
    state, entry = _find(job_id)
    if state != "done":
        raise HTTPException(409, f"job {job_id} is {STATUS_NAMES[state]}, not done")
    manifest = cfg.root / entry["job"]["output"] / "manifest.json"
    return FileResponse(manifest, media_type="application/json", filename=f"{job_id}-manifest.json")
