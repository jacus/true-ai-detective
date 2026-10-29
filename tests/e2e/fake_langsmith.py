"""A tiny LangSmith ingestion server for e2e tests: records the app's traces, nothing leaves the machine.

The app reaches it through the LangSmith SDK's own `LANGSMITH_ENDPOINT` setting, so app code is unchanged.
An empty `/info` means no zstd compression, so traces arrive as plain multipart (one JSON part per run field).
Run: `python tests/e2e/fake_langsmith.py <port>`.
"""

import json
import sys

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()
runs: dict[str, dict] = {}
api_keys: set[str] = set()


@app.get("/info")
def info() -> dict:
    return {}


@app.post("/runs/multipart")
async def ingest(request: Request) -> dict:
    """Parts are named `post.<run id>` / `patch.<run id>` (the run) and `….<field>` (inputs, outputs, …)."""
    api_keys.add(request.headers.get("x-api-key", ""))
    form = await request.form()
    for name, part in form.multi_items():
        raw = part if isinstance(part, str) else (await part.read()).decode()
        _operation, run_id, *field = name.split(".")
        value = json.loads(raw)
        run = runs.setdefault(run_id, {"id": run_id})
        if field:
            run[field[0]] = value
        else:
            run.update(value)
    return {}


@app.get("/received")
def received() -> dict:
    """What the app traced, merged per run, plus the API keys it authenticated with."""
    return {"runs": list(runs.values()), "api_keys": sorted(api_keys)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
