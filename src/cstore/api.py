"""HTTP API for slice requests (Demo 3).

    uvicorn cstore.api:app --port 8000

Runs on the storage node. It never touches the data: a request becomes a job
file in the existing CephFS queue, and the background service (``cstore
service``) runs ``droid_rlds_slice`` on it. Status is read back from the queue.

    POST /jobs        {"keywords": ["put", "mug"], "max_episodes": 20}  ->  {"job_id": ...}
    GET  /jobs/<id>   queued | running | done | failed, plus counts when done
    GET  /health      for scripts that wait for the server to start
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from . import config, jobqueue
from .jobs import Job
from .service import shard_indices

DATASET_DIR = "datasets/droid/rlds/1.0.1"
STATUS = {"pending": "queued", "running": "running", "done": "done", "failed": "failed"}

cfg = config.load()
app = FastAPI(title="cstore slice API")


class SliceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keywords: list[StrictStr] = Field(min_length=1)
    max_episodes: int = Field(default=20, ge=1)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/jobs", status_code=202)
def submit(req: SliceRequest) -> dict:
    keywords = [k.strip().lower() for k in req.keywords if k.strip()]
    if not keywords:
        raise HTTPException(400, "keywords must contain at least one word")
    shards = shard_indices(cfg)
    if not shards:
        raise HTTPException(503, f"no DROID shards loaded under {cfg.root / DATASET_DIR}")
    job_id = f"slice-{uuid.uuid4().hex[:8]}"
    jobqueue.submit(cfg, Job(
        job_id=job_id,
        function="droid_rlds_slice",
        inputs=[f"{DATASET_DIR}/droid_101-train.tfrecord-{i:05d}-of-02048" for i in shards],
        output=f"outputs/slices/{job_id}",
        params={"keywords": keywords, "max_episodes": req.max_episodes},
    ))  # fmt: skip
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def status(job_id: str) -> dict:
    for state, entry in jobqueue.entries(cfg):
        if entry["job"]["job_id"] == job_id:
            break
    else:
        raise HTTPException(404, f"no job {job_id!r}")
    out = {"job_id": job_id, "status": STATUS[state], "output": entry["job"]["output"]}
    result = entry["state"].get("result")
    if result and Path(result).exists():
        worker = json.loads(Path(result).read_text()).get("worker") or {}
        for key in ("episodes_scanned", "episodes_kept", "bytes_scanned", "bytes_out", "error"):
            if key in worker:
                out[key] = worker[key]
    return out
