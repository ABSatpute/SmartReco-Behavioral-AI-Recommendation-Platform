"""LangGraph workflow: analyze -> decide -> retrieve -> evaluate -> refine -> generate -> store.

The graph is the "heart" of SmartReco. It is intentionally explicit so every step
is observable (recorded in agent_runs.steps). Refine loops back into retrieve at
most `agent_max_refine_loops` times; the decide node short-circuits to store when
there is not enough behavioral signal.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    analyze,
    decide,
    evaluate,
    generate,
    refine,
    retrieve,
    store,
)


class AgentState(TypedDict, total=False):
    user_id: int
    trace_id: str
    trigger: str  # event_threshold | digest | manual
    source: str  # auto | daily_digest | manual
    trigger_reason: str | None
    started_at: float

    events: list  # serialized user events
    event_ids: list  # DB ids of those events (for trace correlation)
    event_summary: str
    user_context: dict | None  # known demographics: age, gender
    profile: dict | None  # InterestProfile as dict
    skip_reason: str | None
    queries: list
    candidates: list  # ranked candidate product snapshots
    action: str  # refine | generate
    attempts: int
    result: dict | None  # {summary, narrative, picks}
    run_id: int | None
    recommendation_id: int | None

    llm_calls: int
    total_tokens: int
    fallback_used: bool
    steps: list


def route_after_decide(state: dict) -> str:
    return "store" if state.get("skip_reason") else "retrieve"


def route_after_evaluate(state: dict) -> str:
    return "refine" if state.get("action") == "refine" else "generate"


def build_agent_graph():
    builder = StateGraph(AgentState)
    builder.add_node("analyze", analyze)
    builder.add_node("decide", decide)
    builder.add_node("retrieve", retrieve)
    builder.add_node("evaluate", evaluate)
    builder.add_node("refine", refine)
    builder.add_node("generate", generate)
    builder.add_node("store", store)

    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", "decide")
    builder.add_conditional_edges(
        "decide", route_after_decide, {"retrieve": "retrieve", "store": "store"}
    )
    builder.add_edge("retrieve", "evaluate")
    builder.add_conditional_edges(
        "evaluate", route_after_evaluate, {"refine": "refine", "generate": "generate"}
    )
    builder.add_edge("refine", "retrieve")
    builder.add_edge("generate", "store")
    builder.add_edge("store", END)

    return builder.compile()


agent_graph = build_agent_graph()
