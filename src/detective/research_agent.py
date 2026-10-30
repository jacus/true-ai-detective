"""The research agent (S13c): when the detective decides to research, this agent works on what the client's
latest message needs. It plans, calls the research tools as often as it takes within a budget, and reports.

It is LangChain's own agent (`create_agent`: a model and its tools in a loop, itself a LangGraph graph), set
up with middleware, LangChain's hooks around the loop:

    TodoListMiddleware        adds a `write_todos` tool: the agent writes its plan and ticks it off (`todos`)
    LookFirst                 when the case has photos no one has looked at yet, its first move is to look
                              (the client's photo is the best evidence; a first glance noted on upload isn't)
    show_plan                 sends each plan to the browser as it is written (an AG-UI custom event)
    FileFindings              files what each web tool finds in the evidence locker, as it arrives (`found`)
    ModelCallLimitMiddleware  the budget: at most MODEL_CALLS model calls per run, then it stops
    ToolCallLimitMiddleware   at most TOOL_CALLS tool calls per run, PICTURES of them looking at pictures
                              (a refused call is reported to the model, which then has to report)

It ends with structured output, a `ResearchReport` (OpenAI's JSON schema for the final answer, alongside the
tools). Run it to draw docs/graphs/research-agent.png (`just graphs`).
"""

import operator
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal, NotRequired, cast

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
    ToolCallRequest,
    wrap_tool_call,
)
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from detective import research
from detective.research import CaseContext
from detective.settings import settings

MODEL_CALLS = 8  # per research run: plan, a few rounds of tools, the report
TOOL_CALLS = 16
PICTURES = 6  # examine_image calls: the costliest tool (a vision call per picture)
PLAN_EVENT = "research_plan"  # the custom event carrying the plan to the browser

INSTRUCTIONS = (
    "You research for a private investigator. You are given the conversation with the client and what is "
    "known so far; find out what the client's latest message needs, by yourself: the client expects you to "
    "dig, not to ask them. Search the case file for what the client may have given you; use the web for what "
    "they couldn't. What the client says about their case outweighs earlier notes. To tell what something "
    "in a photo is, look at the photo with a question, find out what event or place it is from, find other "
    "pictures and articles about the same thing, read the pages and look at the pictures (a search result "
    "list is only a lead). Never repeat a search already tried; when the search engines didn't answer, "
    "rewording won't help: work with the other tools and what you already have. For anything that takes "
    "more than one or two steps, plan the steps with write_todos and keep the plan up to date. Stop when you "
    "can answer, or when more research won't help, and report what you found, naming the source (file, "
    "page, picture) of each point."
)


class ResearchReport(BaseModel):
    """What the research found about the client's latest message."""

    answer: str = Field(description="What was found, naming the source (file, page, picture) of each point")
    confidence: Literal["low", "medium", "high"] = Field(description="How sure the answer is")
    open_questions: list[str] = Field(
        description="What is still unknown and worth researching next (by you, not by the client)"
    )


class FindingsState(AgentState):
    """The agent's state, plus what was found and filed during this run, as cited lines."""

    found: Annotated[NotRequired[list[str]], operator.add]  # each tool call appends its lines


class FileFindings(AgentMiddleware[FindingsState, CaseContext]):
    """Files each research tool's result in the case's locker as it arrives (web results as findings; the case
    file's are already there) and adds what was filed to `found`, the run's record for the detective."""

    state_schema = FindingsState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)  # the tool runs
        if not isinstance(result, ToolMessage) or result.name not in research.TOOL_NAMES:
            return result  # e.g. write_todos, or a call the limits refused
        # The run's context (create_agent's context_schema); ToolCallRequest doesn't carry its type.
        context = cast(CaseContext, request.runtime.context)
        case_id = uuid.UUID(context.case_id)
        lines = await research.findings(case_id, request.tool_call, result)
        # A Command updates the agent's state: the tool's message as usual, and what was filed.
        return Command(update={"messages": [result], "found": lines})


class PhotosState(AgentState):
    """The agent's state, plus the case's photos no one has looked at yet with a question (given at start)."""

    unexamined: NotRequired[list[str]]


class LookFirst(AgentMiddleware[PhotosState, CaseContext]):
    """While the case has photos no one has looked at with a question, the model's first call must look at one
    (`examine_image`): a first glance noted on upload, by a small model, can be wrong, and the agent tended to
    trust it and search for what it said instead."""

    state_schema = PhotosState

    async def awrap_model_call(
        self,
        request: ModelRequest[CaseContext],
        handler: Callable[[ModelRequest[CaseContext]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        looked = any(
            call["name"] == research.examine_image.name
            for message in request.state["messages"]
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        )
        if request.state.get("unexamined") and not looked:
            # Forced by name, in the model settings: with the report as OpenAI structured output
            # (ProviderStrategy), create_agent binds the tools with those but without `request.tool_choice`.
            settings = {**request.model_settings, "tool_choice": research.examine_image.name}
            return await handler(request.override(model_settings=settings))
        return await handler(request)


@wrap_tool_call
async def show_plan(
    request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]
) -> ToolMessage | Command:
    """Once the agent has written its plan, send it to the browser at once: ag-ui-langgraph passes custom
    events on as AG-UI CUSTOM events (the detective's state only reaches the browser when a node ends)."""
    result = await handler(request)
    if request.tool_call["name"] == "write_todos":
        await adispatch_custom_event(PLAN_EVENT, request.tool_call["args"]["todos"])
    return result


# Not streamed, like every model call inside the detective's run: none of it is chat.
model = init_chat_model(settings.chat_model, disable_streaming=True)
# ty (beta) can't unify middlewares with different state types into one list; this is the documented usage.
agent = create_agent(  # ty: ignore[no-matching-overload]
    model,
    tools=research.TOOLS,
    system_prompt=INSTRUCTIONS,
    middleware=[
        TodoListMiddleware(),
        LookFirst(),
        show_plan,
        FileFindings(),
        ModelCallLimitMiddleware(run_limit=MODEL_CALLS, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=TOOL_CALLS),
        ToolCallLimitMiddleware(tool_name=research.examine_image.name, run_limit=PICTURES),
    ],
    response_format=ResearchReport,
    context_schema=CaseContext,
    name="research-agent",
)


def report_of(result: dict) -> ResearchReport:
    """The agent's report; when the budget stopped it first, what is left of its plan is still open."""
    if isinstance(result.get("structured_response"), ResearchReport):
        return result["structured_response"]
    unfinished = [todo["content"] for todo in result.get("todos", []) if todo["status"] != "completed"]
    return ResearchReport(
        answer="The research budget ran out before a report; what it found is in the case file.",
        confidence="low",
        open_questions=unfinished,
    )


if __name__ == "__main__":
    out = Path("docs/graphs/research-agent.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    drawing = agent.get_graph()
    # LangChain names a per-tool limit "ToolCallLimitMiddleware[examine_image]", and the Mermaid drawing puts
    # that name in unescaped: the brackets break Mermaid's parser. The picture only: rename it there.
    for key, node in drawing.nodes.items():
        drawing.nodes[key] = node._replace(name=node.name.replace("[", " for ").replace("]", ""))
    drawing.draw_mermaid_png(output_file_path=str(out))
    print(f"Drew {out}")
