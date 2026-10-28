"""The detective's investigation graph. For now a single conversational node; the loop comes in S12."""

from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from detective.settings import settings

SYSTEM_PROMPT = (
    "You are True AI Detective, a private investigator. Be concise and curious: "
    "acknowledge what the client tells you, and ask one sharp follow-up question."
)

model = init_chat_model(settings.chat_model)


async def detective(state: MessagesState) -> dict:
    reply = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
    return {"messages": [reply]}


# ty (beta) doesn't yet match TypedDicts against LangGraph's StateLike protocol; this is the documented usage.
builder = StateGraph(MessagesState)  # ty: ignore[invalid-argument-type]
builder.add_node("detective", detective)
builder.add_edge(START, "detective")
builder.add_edge("detective", END)
# Compiled per request with a checkpointer on the user's own DB connection (cases.case_checkpointer),
# so every checkpoint read and write goes through row-level security.
