"""Interest profile parsing and validation for the agent engine."""
import json
import re
from dataclasses import dataclass, field


@dataclass
class InterestProfile:
    themes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    engagement: str = "low"  # low | medium | high
    urgency: list[str] = field(default_factory=list)
    search_intents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "themes": self.themes,
            "keywords": self.keywords,
            "engagement": self.engagement,
            "urgency": self.urgency,
            "search_intents": self.search_intents,
        }

    @property
    def signal_strength(self) -> int:
        """How much usable signal the profile carries (used by the decide node)."""
        return len(self.themes) + len(self.keywords) + len(self.search_intents)

    def keyword_overlap(self, text: str) -> int:
        lowered = text.lower()
        return sum(1 for kw in self.keywords if kw.lower() in lowered)


def extract_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from an LLM response."""
    if not text:
        return {}
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    pattern = re.compile(r"\{.*\}", re.DOTALL)
    match = pattern.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _as_str_list(value) -> list[str]:
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(item.strip())
        return [s for s in out if s]
    if isinstance(value, str):
        return [p.strip() for p in value.replace("|", ",").split(",") if p.strip()]
    return []


def parse_profile(text: str) -> InterestProfile:
    data = extract_json(text)
    return InterestProfile(
        themes=_as_str_list(data.get("themes")),
        keywords=_as_str_list(data.get("keywords")),
        engagement=str(data.get("engagement") or "low").lower(),
        urgency=_as_str_list(data.get("urgency")),
        search_intents=_as_str_list(data.get("search_intents")),
    )


def heuristic_profile(events: list[dict], event_summary: str) -> InterestProfile:
    """Fallback profile built locally when the analysis LLM call fails."""
    keywords: set[str] = set()
    themes: list[str] = []
    search_intents: list[str] = []
    for event in events:
        etype = event.get("type")
        entity_type = event.get("entity_type")
        entity_id = event.get("entity_id")
        if etype == "search" and entity_id:
            search_intents.append(entity_id.strip()[:120])
            for token in re.findall(r"[a-zA-Z0-9_-]{3,}", entity_id):
                keywords.add(token.lower())
        elif entity_type in ("product", "category") and entity_id:
            for token in re.findall(r"[a-zA-Z0-9_-]{3,}", entity_id):
                keywords.add(token.lower())
    counts = {"product_view": 0, "product_click": 0, "add_to_cart": 0, "search": 0}
    for event in events:
        etype = event.get("type")
        if etype in counts:
            counts[etype] += 1
    urgency = []
    if counts["add_to_cart"] > 0:
        urgency.append("cart intent")
    if counts["product_click"] >= 2:
        urgency.append("active browsing")
    if counts["search"] >= 2:
        urgency.append("focused search")
    if counts["product_view"] >= 5:
        urgency.append("high browse volume")
    engagement = "high" if sum(counts.values()) >= 8 else (
        "medium" if sum(counts.values()) >= 3 else "low"
    )
    if search_intents:
        themes.append("searched for: " + search_intents[0][:80])
    return InterestProfile(
        themes=themes,
        keywords=sorted(kw for kw in keywords if len(kw) > 2)[:10],
        engagement=engagement,
        urgency=urgency,
        search_intents=search_intents[:3],
    )
