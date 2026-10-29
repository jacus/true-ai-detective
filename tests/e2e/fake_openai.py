"""A tiny OpenAI-compatible server for e2e tests: no network, no cost, deterministic replies.

The app reaches it through the OpenAI SDK's own `OPENAI_BASE_URL` setting, so app code is unchanged.
Run: `python tests/e2e/fake_openai.py <port>`.
"""

import json
import re
import sys

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
received: list[dict] = []
# Structured output (non-streamed) is answered per schema: what the "model sees" in a photo, and in a part
# of a document (a fact quoting its first sentence). Tests can queue other answers per schema name.
PHOTO_SIGHTINGS = {
    "sightings": [
        {"kind": "text", "statement": 'A shop sign reads "PIEKARNIA NOWAK"'},
        {"kind": "object", "statement": "A red bicycle leans against the wall"},
    ]
}
queued: dict[str, list[str]] = {}


def first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0][:300]


def document_facts(payload: dict) -> dict:
    """A fact quoting the first sentence of the <document> part the app sent."""
    part = payload["messages"][1]["content"].split(">", 1)[1].rsplit("</document>", 1)[0]
    sentence = first_sentence(part)
    return {
        "facts": [{"kind": "event", "statement": f"The document says: {sentence[:150]}", "quote": sentence}]
    }


def reply_for(user_text: str) -> str:
    return f"Detective on the case. You said: {user_text}"


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> StreamingResponse | dict:
    payload = await request.json()
    received.append(payload)
    if not payload.get("stream"):
        schema = payload["response_format"]["json_schema"]["name"]
        default = PHOTO_SIGHTINGS if schema == "PhotoFacts" else document_facts(payload)
        waiting = queued.get(schema, [])
        content = waiting.pop(0) if waiting else json.dumps(default)
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": payload["model"],
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }
    user_text = next(m["content"] for m in reversed(payload["messages"]) if m["role"] == "user")
    words = reply_for(user_text).split(" ")

    def chunk(delta: dict, finish_reason: str | None = None) -> str:
        body = {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": payload["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(body)}\n\n"

    def stream():
        yield chunk({"role": "assistant", "content": ""})
        for i, word in enumerate(words):
            yield chunk({"content": word if i == 0 else f" {word}"})
        yield chunk({}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/answers/{schema}")
async def queue_answers(schema: str, request: Request) -> None:
    """The next structured answers (raw JSON) for that schema, used in order before the default answer."""
    queued.setdefault(schema, []).extend(await request.json())


@app.get("/received")
def get_received() -> list[dict]:
    """What the app sent to the 'model', so tests can assert on prompts and model choice."""
    return received


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
