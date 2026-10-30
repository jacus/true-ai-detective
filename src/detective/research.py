"""The detective's research tools (S13, S13b), and how what they find is filed in the evidence locker.

Each tool is a plain LangChain tool (`@tool`): its name, docstring and typed arguments become the schema the
chat model is shown (`bind_tools`, in the agent), and LangGraph's `ToolNode` runs the calls the model makes,
in parallel. A tool that fails returns its error to the detective instead of failing the run.

    search_case_file     the case memory (S11), within this case only (the run's context, via `ToolRuntime`)
    web_search           our own SearXNG meta-search service (its JSON API): titles, addresses and snippets
    read_web_page        a page's main text (trafilatura), read like the client's links
    read_archived_page   the Internet Archive's copy of a page closest to a date (Wayback Machine)
    domain_registration  who registered a domain, and when (RDAP, the successor of WHOIS)
    dns_records          a name's DNS records (DNS over HTTPS)
    image_search         pictures on the web (SearXNG's image search): titles, image and page addresses
    examine_image        the vision model answers a question about a picture: a web image, or a client's photo

A web tool that can't get its answer raises `ToolException` and has `handle_tool_error` on, so the tool
itself returns the error as its result, for the detective to read. (The ToolNode would catch any other
exception too, but only after LangChain's `on_tool_error` event carried the exception object to
ag-ui-langgraph, which serialises event data by walking objects: a large one can take forever.)

The web tools return `(content, artifact)` (`response_format="content_and_artifact"`): the content is what
was found; the artifact, which no model ever sees, is where it came from. `findings` files each web result
in the locker as a `finding` evidence with its provenance (tool, input, source address, time), analysed
into cited facts and indexed in the case memory like any document: what the detective finds on the web
becomes part of the case file, so it is recalled later and placed on the board.

`examine_image` decides what to keep itself: the vision model's answer is structured (`ImageAnswer`) and says
whether the picture shows what the question is about. A web picture that does is filed by the tool as a
finding (the image itself, with the answer and clues as its facts); one that doesn't is only reported. The
image's bytes never travel in the tool's result: they would be checkpointed and streamed to the browser.

Every tool talks HTTP, so the e2e tests replay real responses recorded once (vcrpy cassettes).
TODO (security, deferred: learning project): the model picks the addresses these tools fetch (SSRF, see
`evidence_analysis.get_page`), and SearXNG passes the queries on to public search engines.
"""

import asyncio
import base64
import io
import re
import uuid
from datetime import date, datetime
from typing import Any, TypedDict

import httpx
from langchain.chat_models import init_chat_model
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.tools import ToolException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from detective import memory, photo_analysis
from detective.db import SessionLocal
from detective.evidence_analysis import analyse, facts_from, get_page, main_text
from detective.memory import Chunk
from detective.models import Evidence, EvidenceKind, FactKind
from detective.photo_analysis import Found, Sighting
from detective.settings import settings
from detective.storage import INLINE_TYPES, object_key, s3

WEB_RESULTS = 8  # search results passed on
IMAGE_RESULTS = 10  # image search results passed on
PAGE_CHARS = 40_000  # of a page's text: 10 chunks to analyse at most, so a research step stays quick
WAYBACK = "https://web.archive.org"
RDAP = "https://rdap.org"  # redirects to the registry that holds the domain
DNS_OVER_HTTPS = "https://cloudflare-dns.com/dns-query"
DNS_JSON = "application/dns-json"
DNS_TYPES = {1: "A", 28: "AAAA", 15: "MX", 2: "NS", 16: "TXT", 5: "CNAME"}  # numbers as DNS answers give them
TEXT = "text/plain; charset=utf-8"


class CaseContext(BaseModel):
    """Per-run LangGraph runtime context, set by the api: the client's case.

    LangGraph builds it from the run's context dict; ag-ui-langgraph also puts the thread id and other config
    keys in there, which Pydantic ignores (extra="ignore" is its default). Nodes get it as `Runtime`, tools
    as `ToolRuntime`.
    """

    case_id: str = ""


class ImageAnswer(BaseModel):
    """What a picture shows about the detective's question."""

    shows_subject: bool = Field(
        description="True only if the picture clearly shows what the question is about"
    )
    answer: str = Field(
        description="The answer from what is visible, in one or two sentences; say what can't be told"
    )
    clues: list[Sighting] = Field(
        description="Up to 6 visible details the answer rests on (badges, writing, shapes), the most telling "
        "first"
    )


EXAMINE = (
    "You examine pictures for a private investigator. Answer the question from what is actually visible and "
    "name the details your answer rests on. Never guess who people are."
)
# Not streamed, like every structured answer: none of it is chat (see evidence_analysis.extractor).
examiner = init_chat_model(settings.vision_model, disable_streaming=True).with_structured_output(ImageAnswer)


class Source(TypedDict):
    """A web tool's artifact: where its content came from (None when it isn't one address)."""

    url: str | None


async def search_case(query: str, case_id: str) -> list[str]:
    """Search the case memory (hybrid: meaning + exact words), within this case only, as cited lines."""
    retriever = await memory.retriever()
    # The filter keeps the search in this case; row-level security would hide other cases anyway.
    found = await retriever.ainvoke(
        query, filter={"case_id": {"$eq": case_id}}, hybrid_search_config=memory.hybrid(query)
    )
    return [memory.cite(document) for document in found]


async def fetch(url: str, **request: Any) -> httpx.Response:
    """GET for a web tool: a failure becomes a ToolException, which the tool returns as its result."""
    try:
        return await get_page(url, **request)
    except httpx.HTTPError as error:
        raise ToolException(f"Couldn't get {url}: {error}") from None


@tool
async def search_case_file(query: str, runtime: ToolRuntime[CaseContext]) -> str:
    """Search this case's file (the client's evidence and what was found before) for a few words: names,
    places, things, dates."""
    case_id = runtime.context.case_id if runtime.context else ""
    return "\n".join(await search_case(query, case_id)) or "Nothing found."


@tool(response_format="content_and_artifact")
async def web_search(query: str) -> tuple[str, Source]:
    """Search the web. Returns the top results' titles, addresses and snippets."""
    response = await fetch(f"{settings.searx_url}/search", params={"q": query, "format": "json"})
    results = response.json()["results"][:WEB_RESULTS]
    if not results:
        raise ToolException(f"No web results for {query!r}")
    found = "\n\n".join(f"{r['title']}\n{r['url']}\n{r.get('content') or ''}".strip() for r in results)
    return found, {"url": None}  # many addresses: each result carries its own


@tool(response_format="content_and_artifact")
async def read_web_page(url: str) -> tuple[str, Source]:
    """Read a web page's main text, given its http or https address."""
    page = await fetch(url)
    text = main_text(page)[:PAGE_CHARS]
    if not text:
        raise ToolException(f"No readable text at {page.url}")
    return text, {"url": str(page.url)}


@tool(response_format="content_and_artifact", parse_docstring=True)
async def read_archived_page(url: str, around: date) -> tuple[str, Source]:
    """Read a web page as the Internet Archive (Wayback Machine) saved it, in the copy closest to a date.

    Args:
        url: The page's address.
        around: The date to look for a saved copy near.
    """
    # The archive redirects to its closest copy; `id_` asks for the page as it was, without the archive's
    # own toolbar. The copy's address holds when it was saved, e.g. /web/20150101000457id_/…
    page = await fetch(f"{WAYBACK}/web/{around:%Y%m%d}id_/{url}")
    saved = re.search(r"/web/(\d{14})id_/", str(page.url))
    text = main_text(page)[:PAGE_CHARS]
    if not saved or not text:
        raise ToolException(f"The archive has no readable copy of {url}")
    when = datetime.strptime(saved[1], "%Y%m%d%H%M%S")
    return f"Copy saved by the Internet Archive on {when:%Y-%m-%d %H:%M} UTC\n\n{text}", {
        "url": str(page.url)
    }


@tool(response_format="content_and_artifact")
async def domain_registration(domain: str) -> tuple[str, Source]:
    """Who registered an internet domain (e.g. example.com), when, until when, and its name servers."""
    response = await fetch(f"{RDAP}/domain/{domain}", headers={"accept": "application/rdap+json"})
    record = response.json()
    lines = [f"Domain: {record.get('ldhName', domain)}"]
    for entity in record.get("entities", []):
        # A contact is a jCard: ["vcard", [[property, parameters, type, value], …]]; "fn" is the name.
        names = [value for prop, _, _, value in entity.get("vcardArray", ["", []])[1] if prop == "fn"]
        lines.append(
            f"{', '.join(entity.get('roles', ['contact']))}: {', '.join(names) or entity.get('handle')}"
        )
    lines += [f"{event['eventAction']}: {event['eventDate']}" for event in record.get("events", [])]
    if record.get("status"):
        lines.append(f"Status: {', '.join(record['status'])}")
    if record.get("nameservers"):
        lines.append(f"Name servers: {', '.join(ns['ldhName'] for ns in record['nameservers'])}")
    return "\n".join(lines), {"url": str(response.url)}


@tool(response_format="content_and_artifact")
async def dns_records(name: str) -> tuple[str, Source]:
    """A domain name's DNS records: its addresses (A, AAAA), mail servers (MX), name servers (NS) and TXT."""
    responses = await asyncio.gather(
        *(
            fetch(DNS_OVER_HTTPS, params={"name": name, "type": kind}, headers={"accept": DNS_JSON})
            for kind in ["A", "AAAA", "MX", "NS", "TXT"]
        )
    )
    lines: list[str] = []
    for response in responses:
        lines += [
            f"{DNS_TYPES.get(a['type'], a['type'])} {a['data']}" for a in response.json().get("Answer", [])
        ]
    return "\n".join(dict.fromkeys(lines)) or f"No DNS records for {name}", {
        "url": f"{DNS_OVER_HTTPS}?name={name}"
    }


@tool(response_format="content_and_artifact")
async def image_search(query: str) -> tuple[str, Source]:
    """Search the web for pictures. Returns each picture's title, its address and the page it is on."""
    response = await fetch(
        f"{settings.searx_url}/search", params={"q": query, "format": "json", "categories": "images"}
    )
    results = response.json()["results"][:IMAGE_RESULTS]
    if not results:
        raise ToolException(f"No pictures for {query!r}")
    found = "\n\n".join(f"{r['title']}\nimage: {r['img_src']}\nfound on: {r['url']}" for r in results)
    return found, {"url": None}


@tool(response_format="content_and_artifact", parse_docstring=True)
async def examine_image(image: str, question: str, runtime: ToolRuntime[CaseContext]) -> tuple[str, Source]:
    """Look at a picture to answer a question about it, e.g. which make and model a car is. A web picture
    that shows what the question is about is kept in the case file.

    Args:
        image: The picture's web address (e.g. from image_search), or the file name of a photo in the case
            file.
        question: What to find out from the picture.
    """
    case_id = uuid.UUID(runtime.context.case_id if runtime.context else "")
    web = image.startswith(("http://", "https://"))
    content = (await fetch(image)).content if web else await case_photo(case_id, image)
    content_type = picture_type(content, image)
    try:
        seen = await examiner.ainvoke(
            [
                SystemMessage(EXAMINE),
                HumanMessage(
                    content=[
                        {"type": "text", "text": question},
                        {
                            "type": "image",
                            "base64": base64.b64encode(content).decode(),
                            "mime_type": content_type,
                        },
                    ]
                ),
            ]
        )
    except ValidationError as error:  # e.g. a clue breaking Sighting's rules
        raise ToolException(f"The answer about {image} was unusable: {error.errors()[0]['msg']}") from None
    assert isinstance(seen, ImageAnswer)
    if not web:  # already in the case file: what was seen serves this turn
        return "\n".join(f"{line} (from {image}, looked at again)" for line in lines(seen)), {"url": None}
    if not seen.shows_subject:
        return f"{image} doesn't show it: {seen.answer}", {"url": image}
    call: ToolCall = {"name": "examine_image", "args": {"image": image, "question": question}, "id": None}
    evidence = finding(case_id, call, content, content_type, image)
    exif = photo_analysis.read_exif({"content": content, "content_type": content_type, "facts": []})
    answer: Found = {"kind": FactKind.object, "statement": seen.answer}
    clues: list[Found] = [{"kind": c.kind, "statement": c.statement} for c in seen.clues[:6]]
    evidence.facts = facts_from(case_id, [answer, *clues, *exif["facts"]])
    return "\n".join(await store(evidence, content, [])), {"url": image}


def lines(seen: ImageAnswer) -> list[str]:
    return [seen.answer, *(clue.statement for clue in seen.clues[:6])]


async def case_photo(case_id: uuid.UUID, name: str) -> bytes:
    """A photo in this case's locker, by its file name (the latest, if several share it)."""
    async with SessionLocal() as db:  # as the request's client: row-level security applies
        photo = await db.scalar(
            select(Evidence)
            .where(
                Evidence.case_id == case_id, Evidence.label == name, Evidence.content_type.in_(INLINE_TYPES)
            )
            .order_by(Evidence.created_at.desc())
            .limit(1)
        )
    if photo is None:
        raise ToolException(f"There is no photo called {name!r} in the case file")
    stored = await asyncio.to_thread(
        s3.get_object, Bucket=settings.evidence_bucket, Key=object_key(case_id, photo.id)
    )
    return await asyncio.to_thread(stored["Body"].read)


def picture_type(content: bytes, image: str) -> str:
    """The picture's type, read from its bytes (Pillow), whatever the server said; only types we show."""
    if len(content) > settings.evidence_max_bytes:
        raise ToolException(f"{image} is too big")
    try:
        found = Image.open(io.BytesIO(content))
        found.verify()
    except UnidentifiedImageError, SyntaxError, OSError:
        found = None
    content_type = Image.MIME.get(found.format or "") if found else None
    if content_type not in INLINE_TYPES:
        raise ToolException(f"{image} isn't a picture (JPEG, PNG, GIF or WebP)")
    return content_type


WEB_TOOLS = [
    web_search,
    read_web_page,
    read_archived_page,
    domain_registration,
    dns_records,
    image_search,
    examine_image,
]
for web_tool in WEB_TOOLS:  # a field of every tool (`@tool` doesn't take it): a ToolException is their result
    web_tool.handle_tool_error = True
TOOLS = [search_case_file, *WEB_TOOLS]
# Their results are already in the case file, or filed by the tool itself: shown to the detective as they are.
UNFILED = {search_case_file.name, examine_image.name}


def label(call: ToolCall) -> str:
    """A finding's name in the locker: the tool and what it was asked, e.g. "dns_records: example.com"."""
    return f"{call['name']}: {', '.join(str(value) for value in call['args'].values())}"


async def findings(case_id: uuid.UUID, call: ToolCall, result: ToolMessage) -> list[str]:
    """What one tool call found, as the detective is shown it (cited lines); web results are filed first."""
    if result.status == "error":
        return [f"Nothing from {label(call)} ({result.text[:300]})"]
    if call["name"] in UNFILED:
        return result.text.splitlines()
    return await file(case_id, call, result.text, result.artifact)


async def file(case_id: uuid.UUID, call: ToolCall, text: str, source: Any) -> list[str]:
    """File a web tool's text result in the case's locker as a finding, analysed into cited facts like any
    document. Returns what was indexed in the case memory, as cited lines."""
    content = text.encode()
    evidence = finding(case_id, call, content, TEXT, (source or {}).get("url"))
    analysis = await analyse("document", content, TEXT)
    evidence.facts = facts_from(case_id, analysis["facts"])
    return await store(evidence, content, analysis["chunks"])


def finding(
    case_id: uuid.UUID, call: ToolCall, content: bytes, content_type: str, url: str | None
) -> Evidence:
    """A finding with its provenance: the tool, what it was asked, its source address (when: on insert)."""
    return Evidence(
        id=uuid.uuid4(),
        case_id=case_id,
        kind=EvidenceKind.finding,
        label=label(call),
        content_type=content_type,
        size_bytes=len(content),
        url=url,
        tool=call["name"],
        tool_input=call["args"],
    )


async def store(evidence: Evidence, content: bytes, chunks: list[Chunk]) -> list[str]:
    """Upload a finding's content, save it with its facts and index it in the case memory; returns what was
    indexed, as cited lines."""
    # boto3 is blocking: a worker thread, as in the api's upload.
    await asyncio.to_thread(
        s3.upload_fileobj,
        io.BytesIO(content),
        settings.evidence_bucket,
        object_key(evidence.case_id, evidence.id),
        ExtraArgs={"ContentType": evidence.content_type},
    )
    # A session of its own; row-level security applies as for the whole request (db.acting_user).
    async with SessionLocal() as db:
        db.add(evidence)
        await db.commit()
        await db.refresh(evidence)
        stored = await memory.index(evidence, chunks)  # ty: ignore[invalid-argument-type]
    return [memory.cite(document) for document in stored]
