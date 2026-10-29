"""Photo analysis: what an uploaded photo tells the investigation, as facts linked to the photo.

A small LangGraph graph, run once per uploaded photo (draw it: `just graphs`):

    START → read_exif → look_at_photo ──(answer rejected, first time)──→ look_at_photo
                                      └─(accepted, or rejected twice)─→ END

`read_exif` is plain code (Pillow): date, camera and GPS from the photo's metadata. `look_at_photo` asks the
vision model for structured output (a Pydantic model sent as OpenAI's JSON schema). Pydantic validators
add rules JSON Schema can't express; a rejected answer goes back to the model once, with the reason.
"""

import base64
import io
import operator
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from PIL import ExifTags, Image
from pydantic import BaseModel, Field, ValidationError, model_validator

from detective.models import FactKind
from detective.settings import settings

MAX_ATTEMPTS = 2


class Sighting(BaseModel):
    """One thing visible in the photo that could matter to an investigation."""

    kind: Literal[FactKind.text, FactKind.object, FactKind.place] = Field(
        description="text: writing you can read; object: a thing, animal or person; "
        "place: a hint about where the photo was taken"
    )
    statement: str = Field(
        description="One short factual sentence about what is visible. "
        'For text, quote the writing exactly in double quotes, e.g. A sign reads "OPEN".'
    )

    @model_validator(mode="after")
    def follows_the_rules(self) -> Sighting:
        if len(self.statement) > 200:
            raise ValueError("each statement must be under 200 characters")
        if self.kind == FactKind.text and '"' not in self.statement:
            raise ValueError('a text sighting must quote the writing in double quotes: A sign reads "OPEN"')
        return self


class PhotoFacts(BaseModel):
    """What the photo shows."""

    sightings: list[Sighting] = Field(
        description="Up to 10 things worth noting, most useful first; empty if nothing stands out"
    )


class Found(TypedDict):
    """A fact before it is stored."""

    kind: FactKind
    statement: str


class PhotoAnalysis(TypedDict):
    photo: str  # base64
    content_type: str
    facts: Annotated[list[Found], operator.add]  # reducer: each node's facts are appended
    rejected: str | None  # why the model's last answer was refused
    attempts: int


INSTRUCTIONS = (
    "You examine photos for a private investigator. Report only what is actually visible: "
    "readable text, notable objects or people, and hints about the place. Never guess identities."
)
vision = init_chat_model(settings.chat_model).with_structured_output(PhotoFacts)


def read_exif(state: PhotoAnalysis) -> dict:
    exif = Image.open(io.BytesIO(base64.b64decode(state["photo"]))).getexif()
    Base, GPS = ExifTags.Base, ExifTags.GPS
    facts: list[Found] = []
    taken = exif.get_ifd(ExifTags.IFD.Exif).get(Base.DateTimeOriginal) or exif.get(Base.DateTime)
    if taken:  # EXIF writes "2026:10:01 21:14:05", in the camera's own clock
        when = taken.replace(":", "-", 2)
        facts.append({"kind": FactKind.taken_at, "statement": f"Taken {when} (camera clock)"})
    camera = " ".join(str(exif[tag]).strip("\x00 ") for tag in (Base.Make, Base.Model) if tag in exif)
    if camera:
        facts.append({"kind": FactKind.camera, "statement": f"Camera: {camera}"})
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    if GPS.GPSLatitude in gps and GPS.GPSLongitude in gps:
        latitude = degrees(gps[GPS.GPSLatitude], gps.get(GPS.GPSLatitudeRef, "N"))
        longitude = degrees(gps[GPS.GPSLongitude], gps.get(GPS.GPSLongitudeRef, "E"))
        facts.append({"kind": FactKind.location, "statement": f"GPS {latitude:.5f}, {longitude:.5f}"})
    return {"facts": facts}


def degrees(dms: tuple[float, float, float], ref: str) -> float:
    """EXIF stores degrees, minutes, seconds plus a hemisphere letter."""
    value = float(dms[0]) + float(dms[1]) / 60 + float(dms[2]) / 3600
    return -value if ref in ("S", "W") else value


async def look_at_photo(state: PhotoAnalysis) -> dict:
    messages: list[BaseMessage] = [
        SystemMessage(INSTRUCTIONS),
        HumanMessage(
            content=[
                {"type": "text", "text": "What does this photo show?"},
                {"type": "image", "base64": state["photo"], "mime_type": state["content_type"]},
            ]
        ),
    ]
    if state["rejected"]:
        messages.append(HumanMessage(f"Your last answer was rejected: {state['rejected']}. Answer again."))
    attempts = state["attempts"] + 1
    try:
        seen = await vision.ainvoke(messages)
    except ValidationError as error:  # the OpenAI SDK parses the answer into PhotoFacts, validators included
        return {"rejected": "; ".join(e["msg"] for e in error.errors()), "attempts": attempts}
    assert isinstance(seen, PhotoFacts)
    found: list[Found] = [{"kind": s.kind, "statement": s.statement} for s in seen.sightings[:10]]
    return {"facts": found, "rejected": None, "attempts": attempts}


def retry_if_rejected(state: PhotoAnalysis) -> Literal["look_at_photo", "__end__"]:
    # The Literal return type is how LangGraph knows (and draws) where this edge can go.
    return "look_at_photo" if state["rejected"] and state["attempts"] < MAX_ATTEMPTS else END  # ty: ignore[invalid-return-type]


# ty (beta) doesn't yet match TypedDicts against LangGraph's StateLike protocol; this is the documented usage.
builder = StateGraph(PhotoAnalysis)  # ty: ignore[invalid-argument-type]
builder.add_node(read_exif)
builder.add_node(look_at_photo)
builder.add_edge(START, "read_exif")
builder.add_edge("read_exif", "look_at_photo")
builder.add_conditional_edges("look_at_photo", retry_if_rejected)
graph = builder.compile(name="photo-analysis")


async def analyse(photo: bytes, content_type: str) -> list[Found]:
    result = await graph.ainvoke(
        {
            "photo": base64.b64encode(photo).decode(),
            "content_type": content_type,
            "facts": [],
            "rejected": None,
            "attempts": 0,
        }
    )
    return result["facts"]


if __name__ == "__main__":
    out = Path("docs/graphs/photo-analysis.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    graph.get_graph().draw_mermaid_png(output_file_path=str(out))
    print(f"Drew {out}")
