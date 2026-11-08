"""Python library interface: what researchers call from their own code.

    import cstore_client as cc
    job = cc.submit(tasks=["put"], objects=["cup"], drop_idle=True,
                    cameras=["wrist", "exterior_1"], frame_stride=15)
    job.wait()
    job.download("./my_slice")

`server` defaults to the CSTORE_SERVER environment variable, else "mock".
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .request import SliceRequest
from .transport import Transport, TransportError, make_transport

TERMINAL = ("done", "failed")


class JobHandle:
    def __init__(self, job_id: str, transport: Transport):
        self.job_id = job_id
        self.transport = transport

    def status(self) -> dict[str, Any]:
        return self.transport.status(self.job_id)

    def wait(self, poll: float = 1.0, timeout: float | None = 600.0,
             on_update: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Poll until the job is done or failed; return the final status."""
        start = time.monotonic()
        last = None
        while True:
            st = self.status()
            if on_update and st.get("status") != last:
                on_update(st)
            last = st.get("status")
            if last in TERMINAL:
                return st
            if timeout is not None and time.monotonic() - start > timeout:
                raise TransportError(f"job {self.job_id} still {last} after {timeout:.0f}s")
            time.sleep(poll)

    def download(self, dest: str | Path) -> list[Path]:
        return self.transport.download(self.job_id, Path(dest))

    def __repr__(self) -> str:
        return f"JobHandle({self.job_id!r})"


def submit(request: SliceRequest | None = None, server: str | None = None,
           transport: Transport | None = None, **kwargs: Any) -> JobHandle:
    """Submit a slice request. Pass a SliceRequest, or keyword arguments
    accepted by SliceRequest.build (tasks, objects, labs, episode_ids,
    max_episodes, drop_idle, cameras, frame_stride, output_format)."""
    if request is None:
        request = SliceRequest.build(**kwargs)
    elif kwargs:
        raise TypeError("pass either a SliceRequest or keyword arguments, not both")
    request.validate()
    t = transport or make_transport(server)
    return JobHandle(t.submit(request.to_dict()), t)


def job(job_id: str, server: str | None = None, transport: Transport | None = None) -> JobHandle:
    """Reattach to a job submitted earlier."""
    return JobHandle(job_id, transport or make_transport(server))


def options(server: str | None = None, transport: Transport | None = None) -> dict[str, list[str]]:
    return (transport or make_transport(server)).options()
