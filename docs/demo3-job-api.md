# Demo 3: keyword slice API (status 2026-11-08)

**Status:** works locally on one node and is tested. Not yet deployed on AWS. Not multi-node.

A user sends keywords over HTTP. The storage side keeps the DROID episodes whose instruction contains **all** of them and writes a smaller RLDS dataset next to the data. Only the request and a status summary cross the network. The API never runs code or touches data: it files a job into the existing queue, and the Demo 2 background service runs the filter in a worker. No ssh.

```
bin/cstore-client --keywords put,mug --max-episodes 20
  → POST /jobs {"keywords": ["put", "mug"], "max_episodes": 20}
  → job file in queue/pending → cstore service → worker runs droid_rlds_slice
  → outputs/slices/<job_id>/   (kept episodes, byte for byte, + manifest.json)
```

## Files

| File | Role |
|---|---|
| `bin/cstore-client` | Client: sends the request, follows the job, prints the summary (stdlib only) |
| `src/cstore/api.py` | API server (FastAPI): `POST /jobs`, `GET /jobs/<id>`, `GET /health` |
| `src/cstore/functions/droid_rlds_slice.py` | The filter, run by the worker |
| `src/cstore/functions/__init__.py` | +1 line registering the filter (the only change to Demo 2 code) |
| `experiments/11_demo3_serve.sh` | Starts the API server + service locally |
| `tests/test_slice_e2e.py` | End-to-end test on synthetic shards |

## Rules

- **Matching:** an episode is kept if one of its instructions contains every keyword, as whole words, ignoring case.
- **Size limit:** `max_episodes` defaults to 20.
- **Bad requests are rejected:** missing keywords, extra fields, `max_episodes` < 1.
- **No matches:** the output folder holds only `manifest.json`.

## Run locally

Needs a Python with `tensorflow-cpu==2.21.0 tensorflow-datasets==4.9.10 fastapi uvicorn`, and shards loaded with `bin/cstore load-droid --shards 2`.

```
# terminal 1: the storage node (stays running; Ctrl+C stops it)
PYTHON=<venv>/bin/python CSTORE_ROOT=~/cstore-local experiments/11_demo3_serve.sh
# terminal 2: the user
bin/cstore-client --keywords put,mug
# tests
<venv>/bin/python -m unittest tests.test_slice_e2e tests.test_core -v
```

**Results:**
- All 6 new tests and the 20 existing tests pass.
- On real shard 2 (649 MB, 39 episodes), `put,mug` ran in 8.8 s and produced a 69.2 MB slice.

## Not done yet

- **AWS deployment:**
  - rebuild the worker image so it includes the filter;
  - run the API as a systemd service;
  - open port 8000 only to the user's IP and add a token;
  - drop `--watch` from `cstore-service`, or let its conversions finish first.
- **Trimming inside episodes:** cameras, frame stride, idle frames.
- **Multi-node placement:** splitting a request by where episodes live.
