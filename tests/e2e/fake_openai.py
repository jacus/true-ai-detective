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
# A request offering tools (the detective's research) is answered with tool calls: a search of the case file
# for "the case" unless a test queued its own rounds of calls.
PHOTO_SIGHTINGS = {
    "sightings": [
        {"kind": "text", "statement": 'A shop sign reads "PIEKARNIA NOWAK"'},
        {"kind": "object", "statement": "A red bicycle leans against the wall"},
    ]
}
queued: dict[str, list[str]] = {}
tool_rounds: list[list[dict]] = []
DEFAULT_ROUND = [{"name": "search_case_file", "args": {"query": "the case"}}]


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


def completion(payload: dict, message: dict, finish_reason: str) -> dict:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": payload["model"],
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def tool_calls(calls: list[dict]) -> list[dict]:
    return [
        {
            "id": f"call_{n}",
            "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call["args"])},
        }
        for n, call in enumerate(calls)
    ]


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> StreamingResponse | dict:
    payload = await request.json()
    received.append(payload)
    if payload.get("tools"):
        calls = tool_rounds.pop(0) if tool_rounds else DEFAULT_ROUND
        message = {"role": "assistant", "content": None, "tool_calls": tool_calls(calls)}
        return completion(payload, message, "tool_calls")
    if not payload.get("stream"):
        schema = payload["response_format"]["json_schema"]["name"]
        answers = {
            "PhotoFacts": lambda: PHOTO_SIGHTINGS,
            "DocumentFacts": lambda: document_facts(payload),
        }
        default = answers.get(schema, lambda: case_board(payload))()
        waiting = queued.get(schema, [])
        content = waiting.pop(0) if waiting else json.dumps(default)
        return completion(payload, {"role": "assistant", "content": content}, "stop")
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


decisions: list[dict] = []
ACTIONS = ["research", "ask", "consult", "conclude"]


@app.post("/v1/decisions")
async def decide(request: Request) -> dict:
    """OpenAI's Decisions API (what AI Gateway serves JEV through): "ask" unless a test queued decisions."""
    payload = await request.json()
    received.append(payload)
    queued_decision = decisions.pop(0) if decisions else {"choice": "ask"}
    usage = {
        "input_tokens": 100,
        "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
        "output_tokens": 10,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 110,
    }
    if queued_decision.get("refusal"):
        return {
            "model": payload["model"],
            "answers": [{"type": "refusal", "name": "next_step"}],
            "usage": usage,
        }
    choice = queued_decision["choice"]
    default = {action: 0.9 if action == choice else 0.1 / 3 for action in ACTIONS}
    probabilities = queued_decision.get("probabilities", default)
    return {
        "model": payload["model"],
        "answers": [
            {
                "type": "choice",
                "name": "next_step",
                "choice": choice,
                "confidence": probabilities[choice],
                "probabilities": [{"value": a, "probability": p} for a, p in probabilities.items()],
            }
        ],
        "usage": usage,
    }


@app.post("/decisions")
async def queue_decisions(request: Request) -> None:
    """The next decisions, e.g. [{"choice": "research"}, {"choice": "conclude", "probabilities": {...}}]."""
    decisions.extend(await request.json())


@app.post("/tool_calls")
async def queue_tool_calls(request: Request) -> None:
    """The next research rounds, each a list of calls, e.g. [[{"name": "web_search", "args": {...}}]]."""
    tool_rounds.extend(await request.json())


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
