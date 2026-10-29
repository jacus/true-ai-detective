"""The detective's next step, decided by a decision model (JEV) rather than a chat model.

A decision model answers typed questions about some state: here one `choice` question, "what next?", with
the four options. It returns the choice and a probability for every option, fast and for a fraction of a
cent. It is called with the official OpenAI SDK's Decisions API, pointed at Vercel AI Gateway, and traced
with LangSmith's `@traceable` (it isn't a LangChain model, so LangChain can't trace it).
"""

from typing import Literal, TypedDict, cast

from langsmith import traceable
from openai import AsyncOpenAI
from openai.types.decision_create_params import QuestionQuestionParamChoice

from detective.settings import settings

Action = Literal["research", "ask", "consult", "conclude"]
ACTIONS: dict[Action, str] = {
    "research": "look for something specific that may answer the client: in the case file or on the web "
    "(search, web pages, archived pages, domain and DNS records)",
    "ask": "ask the client for information the case file lacks",
    "consult": "ask the client to confirm an assumption before going on",
    "conclude": "what has been found is enough to answer the client",
}
NEXT_STEP: QuestionQuestionParamChoice = {
    "type": "choice",
    "name": "next_step",
    "instructions": "What should the detective do next about the client's latest message?",
    "choices": [{"value": action, "description": description} for action, description in ACTIONS.items()],
}

client = AsyncOpenAI(base_url=settings.decisions_base_url, api_key=settings.ai_gateway_api_key or "unset")


class Decision(TypedDict):
    action: Action
    probabilities: dict[Action, float]


@traceable(name="decide-next-step", run_type="llm", metadata={"ls_model_name": settings.decision_model})
async def next_step(situation: str) -> Decision:
    """Ask the decision model what to do next, given a description of the situation."""
    decision = await client.decisions.create(
        model=settings.decision_model, input=situation, questions=[NEXT_STEP]
    )
    [answer] = decision.answers
    if answer.type != "choice" or answer.choice not in ACTIONS:  # a refusal: asking the client is safest
        return {"action": "ask", "probabilities": {}}
    probabilities = {cast(Action, p.value): p.probability for p in answer.probabilities}
    return {"action": answer.choice, "probabilities": probabilities}
