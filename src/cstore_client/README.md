# cstore_client: job API client (Demo 3)

Ask the cluster for a custom slice of DROID. The storage nodes filter and trim the data where it lives and send back only the result. Standard library only (Python 3.10+). Spec: the project doc "Job API v0 draft".

## Quick start (mock server, no network)

```bash
bin/cstore-client options                        # what you can select
bin/cstore-client submit --tasks put --objects cup --drop-idle \
    --cameras wrist,exterior_1 --frame-stride 15 --download ./slice
bin/cstore-client submit -i                      # pick options from menus
bin/cstore-client init request.json              # write a template, then:
bin/cstore-client submit --config request.json
bin/cstore-client --json submit --tasks put --wait   # machine-readable
```

Exit codes: 0 success, 1 job failed or server unreachable, 2 bad request.

From Python:

```python
import cstore_client as cc
job = cc.submit(tasks=["put"], objects=["cup"], drop_idle=True,
                cameras=["wrist", "exterior_1"], frame_stride=15)
job.wait()
job.download("./slice")
```

## Mock vs. real server

| Setting | What happens |
|---|---|
| `--server mock` (default) | No network. Prints the HTTP call it would send and simulates the server: matching episodes from a fake 3-node, 12-shard catalog, the per-node jobs it would queue (same shape as `cstore.jobs.Job`), and a byte estimate (in place vs. pulling whole shards). Job state lives in a temp folder (`CSTORE_MOCK_DIR`); `CSTORE_MOCK_DELAY=0` makes jobs finish instantly. The "result" is a manifest JSON, not data. |
| `--server http://<storage-ip>:8000` | Real HTTP to the API server: `POST /jobs`, `GET /jobs/<id>`, `GET /jobs/<id>/result`, `GET /options`. Optional `CSTORE_TOKEN` is sent as a Bearer token. Tested against a stub server in the unit tests; the real server doesn't exist yet. |

Set `CSTORE_SERVER` to change the default without editing anything.

## Layout

| File | Role |
|---|---|
| `request.py` | `SliceRequest`: select / trim / output, defaults, validation, JSON load and save |
| `transport.py` | `MockTransport` and `HttpTransport` (same four methods), plus the mock catalog and planner |
| `client.py` | Library API: `submit()`, `JobHandle.wait()` / `.download()`, `job()`, `options()` |
| `cli.py` | Command line: flags, `--config`, `-i`, `--json`, `--dry-run`, `init` |

Tests: `python3 -m unittest discover -s tests -v` (`tests/test_client.py`).

## Mock assumptions to replace later

- The episode catalog, instructions, and labs are fake. The real values come from the location index (D3) through `GET /options`.
- Byte estimates use Demo 2's shard size (about 650 MB / 40 episodes), images about 95% of bytes, and a **placeholder** 30% idle share.
- Matching rule (v0): an episode matches if its instruction contains any task word AND any object word, its lab is listed, and its id is listed. An empty list means "any".
