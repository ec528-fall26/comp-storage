"""Client for the computational-storage job API (Demo 3).

Library:  import cstore_client as cc; cc.submit(tasks=["put"], ...)
CLI:      bin/cstore-client --help
"""

from .client import JobHandle, job, options, submit
from .request import CAMERAS, RequestError, SliceRequest
from .transport import HttpTransport, MockTransport, TransportError, make_transport

__all__ = [
    "CAMERAS", "HttpTransport", "JobHandle", "MockTransport", "RequestError",
    "SliceRequest", "TransportError", "job", "make_transport", "options", "submit",
]
