"""Prompt templates for the agent engine nodes."""

ANALYZE_SYSTEM = """You are a behavioral analyst for a personalized recommendation engine. Given a user's recent activity, produce a compact JSON profile of their interests.

Respond with ONLY a JSON object using exactly these keys:
{
  "themes": ["2 to 4 short topic phrases"],
  "keywords": ["6 to 10 lowercase retrieval keywords"],
  "engagement": "low" | "medium" | "high",
  "urgency": ["short signals such as cart intent, repeat visits, active search"],
  "search_intents": ["1 to 3 natural-language queries the user would type into a search box"]
}

Rules:
- Base everything strictly on the activity provided. Never invent topics.
- Keywords should be generic enough to match a real product catalog.
- Do not add commentary outside the JSON."""


def _context_block(user_context: dict | None) -> str:
    if not user_context:
        return ""
    parts = []
    if user_context.get("age"):
        parts.append(f"age {user_context['age']}")
    if user_context.get("gender"):
        parts.append(f"gender {user_context['gender']}")
    if not parts:
        return ""
    return "Known user demographics: " + ", ".join(parts) + ".\n"


def analyze_user_prompt(event_summary: str, event_count: int, user_context: dict | None = None) -> str:
    return (
        "Analyze the following recent activity of a user on a product marketplace.\n"
        f"Total activity entries: {event_count}\n\n"
        f"{_context_block(user_context)}"
        "Recent activity:\n"
        f"{event_summary or '(no activity recorded)'}\n\n"
        "Return the JSON profile now."
    )


GENERATE_SYSTEM = """You are a persuasive personal shopping assistant. A recommendation engine retrieved candidate products that match a user's recent browsing. Write a recommendation that feels personal and specific to their journey.

Respond with ONLY a JSON object using exactly these keys:
{
  "summary": "one short headline, max 12 words",
  "narrative": "a persuasive paragraph of 80 to 160 words that references the user's actual activity (their searches, topics, time spent, cart behavior), frames benefits, and ends with a clear call to action",
  "picks": [
    {"product_id": <int>, "rationale": "one line on why it fits, referencing the user's activity"}
  ]
}

Rules:
- Pick ONLY 3 or 4 product ids from the candidates provided. Never invent products or ids.
- Every product_id must appear in the candidates list.
- Ground every claim in the user profile and the candidate product facts. No generic "popular products" copy."""


def generate_user_prompt(profile: dict, candidates: list[dict], user_context: dict | None = None) -> str:
    lines = ["CANDIDATE PRODUCTS:"]
    for c in candidates:
        fields = (
            f"- id={c['id']} title={c['title']!r} category={c['category']!r} "
            f"price={c['price']} stars={c['stars']} best_seller={c['best_seller']}"
        )
        lines.append(fields)
    return (
        "Here is the user profile built from their recent activity:\n"
        f"{profile}\n\n"
        f"{_context_block(user_context)}"
        + "\n".join(lines)
        + "\n\nReturn the recommendation JSON now."
    )


FALLBACK_RATIONALE = (
    "Matches your interest in {theme} and ranks highest among the candidates we retrieved."
)
