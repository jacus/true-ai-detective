"""A tiny OpenAI-compatible server for e2e tests: no network, no cost, deterministic replies.

The app reaches it through the OpenAI SDK's own `OPENAI_BASE_URL` setting, so app code is unchanged.
Run: `python tests/e2e/fake_openai.py <port>`.
"""

import base64
import json
import math
import re
import struct
import sys
import zlib

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
received: list[dict] = []
# Structured output (non-streamed) is answered per schema: what the "model sees" in a photo, in a part of a
# document (a fact quoting its first sentence), and the case board (a timeline entry per numbered fact, a lead
# for the newest). Tests can queue other answers per schema name.
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


def case_board(payload: dict) -> dict:
    """A board built from the <facts> the app sent (lines "F1: ...")."""
    facts_block = payload["messages"][1]["content"].split("<facts>", 1)[1].split("</facts>", 1)[0]
    facts = [line.split(": ", 1)[1] for line in facts_block.strip().splitlines() if line.startswith("F")]
    return {
        "leads": [{"what": f"Follow up on F{len(facts)}", "why": "newest evidence"}] if facts else [],
        "hypotheses": [],
        "timeline": [
            {"when": "as reported", "what": fact[:80], "fact": n} for n, fact in enumerate(facts, 1)
        ],
        "contradictions": [],
    }


def reply_for(user_text: str) -> str:
    return f"Detective on the case. You said: {user_text}"


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> StreamingResponse | dict:
    payload = await request.json()
    received.append(payload)
    if not payload.get("stream"):
        schema = payload["response_format"]["json_schema"]["name"]
        answers = {"PhotoFacts": lambda: PHOTO_SIGHTINGS, "DocumentFacts": lambda: document_facts(payload)}
        default = answers.get(schema, lambda: case_board(payload))()
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


DIMENSIONS = 1536  # as text-embedding-3-small
STOPWORDS = {"the", "and", "was", "who", "what", "with", "from", "that", "this", "for", "are", "did", "does"}


def embedding(text: str) -> list[float]:
    """Bag of words hashed into a unit vector: texts sharing words are close (cosine), others far apart."""
    vector = [0.0] * DIMENSIONS
    for word in re.findall(r"[a-z0-9@.+]+", text.lower()):
        word = word.strip(".")
        if len(word) >= 3 and word not in STOPWORDS:
            vector[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0
    vector[0] += 0.01  # never all zeros: a cosine distance to a zero vector is undefined
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> dict:
    payload = await request.json()
    received.append(payload)
    texts = [payload["input"]] if isinstance(payload["input"], str) else payload["input"]
    as_base64 = payload.get("encoding_format") == "base64"  # the OpenAI SDK's default: packed float32s

    def encoded(vector: list[float]) -> list[float] | str:
        return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode() if as_base64 else vector

    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": encoded(embedding(t))}
            for i, t in enumerate(texts)
        ],
        "model": payload["model"],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


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
