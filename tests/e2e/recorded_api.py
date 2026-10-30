"""The api for research tests: the server `fastapi run` starts (uvicorn), with the web replayed by vcrpy.

Every request the api makes to the web, and to SearXNG (`SEARX_URL`), is answered from a vcrpy cassette that
was recorded once from the real services (`just record-research`). Everything else local passes through:
the fake OpenAI and LangSmith servers, S3 (RustFS). With VCR_RECORD_MODE=none (the default) a request that
isn't in the cassette fails, so these tests never reach the web either.
Run: `python tests/e2e/recorded_api.py <cassette> <port>`.
"""

import os
import signal
import sys

import uvicorn
import vcr
from vcr.request import Request

SEARX = os.environ["SEARX_URL"]


def recorded(request: Request) -> Request | None:
    """Which requests the cassette is for (vcrpy passes the others through: returning None ignores them)."""
    local = request.host in ("127.0.0.1", "localhost") and not request.uri.startswith(SEARX)
    return None if local else request


# Headers that say who recorded the cassette (it is public): e.g. Wikimedia echoes the caller's IP address.
IDENTIFYING = {"x-client-ip", "set-cookie"}


def scrubbed(response: dict) -> dict:
    response["headers"] = {k: v for k, v in response["headers"].items() if k.lower() not in IDENTIFYING}
    return response


recorder = vcr.VCR(
    record_mode=os.environ.get("VCR_RECORD_MODE", "none"),
    before_record_request=recorded,
    before_record_response=scrubbed,
    filter_headers=["cookie"],  # and the cookies sent back
    decode_compressed_response=True,  # the cassette is readable text, so what the tools saw can be reviewed
)

if __name__ == "__main__":
    cassette, port = sys.argv[1], int(sys.argv[2])
    # Uvicorn shuts down gracefully on SIGTERM, then hands the signal to the handler that was there before it:
    # this one ends the process through the `with` below, so a recording cassette is written on the way out.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    # Repeats: each test runs its research again, from the same recording.
    with recorder.use_cassette(cassette, allow_playback_repeats=True):
        uvicorn.run("detective.main:app", host="127.0.0.1", port=port, log_level="warning")
