"""Aggregations backing the admin observability dashboard and stats API.

Everything is computed on-demand from ``agent_runs``/``user_events`` so there is
no background pipeline to maintain. Cost figures are *estimates* derived from a
small per-model price table; adjust ``MODEL_PRICE_PER_M`` when billing changes.
"""
from __future__ import annotations

import math
import statistics
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentRun, User, UserEvent
from app.utils import utcnow_naive

# --------------------------------------------------------------------------- #
# cost model (estimates)
# --------------------------------------------------------------------------- #
MODEL_PRICE_PER_M = {
    "openai/gpt-4o": 7.0,
    "openai/gpt-4o-mini": 1.5,
    "minimax/m2-her": 1.5,
}
DEFAULT_PRICE_PER_M = 2.5

RANGE_LABELS = {
    "24h": "Last 24 hours",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "all": "All time",
}
RANGE_HOURS = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}


def cost_usd(tokens: int, model: str | None = None) -> float:
    return (tokens or 0) * MODEL_PRICE_PER_M.get(model or "", DEFAULT_PRICE_PER_M) / 1_000_000.0


def _model_for_node(node: str) -> str:
    if node == "analyze":
        return settings.analysis_model
    return settings.llm_model


def _run_cost(steps: list[dict], total_tokens: int) -> float:
    """Approximate a run's cost from per-step token deltas + node model pricing.

    Falls back to a blended rate for legacy runs that only store totals.
    """
    enriched = any("tokens_delta" in s for s in steps if s.get("node") != "meta")
    if not enriched:
        return cost_usd(total_tokens)
    total = 0.0
    for s in steps:
        if s.get("node") == "meta":
            continue
        delta = s.get("tokens_delta")
        if delta:
            total += delta * MODEL_PRICE_PER_M.get(
                _model_for_node(s.get("node", "")), DEFAULT_PRICE_PER_M
            ) / 1_000_000.0
    return round(total, 4)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return ordered[lo]
    return int(ordered[lo] * (hi - k) + ordered[hi] * (k - lo))


def _error_label(error: str) -> str:
    if error.startswith("skipped:"):
        return "skipped"
    if error.startswith("unhandled:"):
        return "unhandled exception"
    return error[:60]


def is_skip_run(error: str | None) -> bool:
    """Runs that intentionally declined to recommend (not real failures)."""
    return bool(error and (error.startswith("skipped:") or error == "no candidates to present"))


def run_status(error: str | None) -> str:
    """Classify a run as ok / skipped / failed, matching the observability UI."""
    if is_skip_run(error):
        return "skipped"
    return "failed" if error else "ok"


def _buckets(range_key: str, now):
    """Return (bucket_start_times, bucket_seconds, hourly, label_fn)."""
    if range_key == "all":
        # For "all" we bucket daily from the earliest run.
        return None
    hours = RANGE_HOURS.get(range_key, 24 * 7)
    hourly = range_key == "24h"
    n = 24 if hourly else hours // 24
    starts = []
    for i in range(n):
        if hourly:
            b = now - timedelta(hours=n - 1 - i)
            starts.append(b.replace(minute=0, second=0, microsecond=0))
        else:
            b = now - timedelta(days=n - 1 - i)
            starts.append(b.replace(hour=0, minute=0, second=0, microsecond=0))
    label = lambda b: f"{b.hour:02d}h" if hourly else b.strftime("%m-%d")
    return starts, (3600 if hourly else 86400), label


# --------------------------------------------------------------------------- #
# main aggregation
# --------------------------------------------------------------------------- #
def _new_node() -> dict:
    return {
        "runs": 0,
        "ok": 0,
        "fallback": 0,
        "skip": 0,
        "fail": 0,
        "durations": [],
        "tokens": [],
        "calls": [],
    }


def agent_metrics(
    db: Session,
    range_key: str = "7d",
    trigger: str | None = None,
    status: str | None = None,
) -> dict:
    now = utcnow_naive()
    start = None
    if range_key != "all":
        start = now - timedelta(hours=RANGE_HOURS.get(range_key, 24 * 7))

    q = db.query(AgentRun)
    if start is not None:
        q = q.filter(AgentRun.created_at >= start)
    if trigger:
        q = q.filter(AgentRun.trigger == trigger)
    if status == "ok":
        q = q.filter(AgentRun.error.is_(None))
    elif status in ("failed", "skipped"):
        q = q.filter(AgentRun.error.isnot(None))
    runs = q.order_by(AgentRun.created_at.desc()).all()

    if status == "failed":
        runs = [r for r in runs if not is_skip_run(r.error)]
    elif status == "skipped":
        runs = [r for r in runs if is_skip_run(r.error)]

    if range_key == "all":
        start = runs[-1].created_at if runs else now
        # Bucket from earliest run's day to today.
        n_days = max(1, (now.date() - start.date()).days + 1)
        starts = []
        for i in range(n_days):
            day = (now - timedelta(days=n_days - 1 - i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            starts.append(day)
        bucket_sec, hourly = 86400, False
        label = lambda b: b.strftime("%m-%d")
    else:
        starts, bucket_sec, label = _buckets(range_key, now)
        hourly = range_key == "24h"

    indexed = {b: i for i, b in enumerate(starts)}
    n = len(starts)
    ok_series = [0] * n
    fail_series = [0] * n
    skip_series = [0] * n
    token_series = [0] * n
    cost_series = [0.0] * n

    nodes: dict[str, dict] = {}
    errors: dict[str, dict] = {}
    triggers: dict[str, int] = {}
    fallback_runs = 0
    skipped_runs = 0
    durations: list[int] = []
    token_list: list[int] = []
    call_list: list[int] = []
    total_cost = 0.0

    first_bucket = starts[0] if starts else start

    def _bucket_idx(created_at):
        if not starts or created_at < first_bucket:
            return 0
        if created_at > starts[-1] + timedelta(seconds=bucket_sec):
            return n - 1
        return (int((created_at - first_bucket).total_seconds()) // bucket_sec) % n

    for run in runs:
        idx = _bucket_idx(run.created_at)
        steps = run.steps if isinstance(run.steps, list) else []
        ok = run.error is None
        skip = (not ok) and is_skip_run(run.error)
        if ok:
            ok_series[idx] += 1
        elif skip:
            skip_series[idx] += 1
        else:
            fail_series[idx] += 1
        token_series[idx] += run.total_tokens
        run_cost = _run_cost(steps, run.total_tokens)
        cost_series[idx] += run_cost
        total_cost += run_cost
        durations.append(run.duration_ms)
        token_list.append(run.total_tokens)
        call_list.append(run.llm_calls)
        triggers[run.trigger or "auto"] = triggers.get(run.trigger or "auto", 0) + 1

        meta = next((s for s in steps if s.get("node") == "meta"), {})
        meta_extra = meta.get("extra") or {}
        if meta_extra.get("fallback_used") or any(
            s.get("status") == "fallback" for s in steps if s.get("node") != "meta"
        ):
            fallback_runs += 1
        if skip:
            skipped_runs += 1

        # per-node aggregation from enriched steps
        for s in steps:
            if s.get("node") == "meta":
                continue
            node = s.get("node") or "?"
            st = s.get("status") or "ok"
            A = nodes.setdefault(node, _new_node())
            A["runs"] += 1
            A[st if st in ("ok", "fallback", "skip") else "fail"] += 1
            if isinstance(s.get("duration_ms"), (int, float)):
                A["durations"].append(int(s["duration_ms"]))
            if isinstance(s.get("tokens_delta"), (int, float)):
                A["tokens"].append(int(s["tokens_delta"]))
            if isinstance(s.get("llm_delta"), (int, float)):
                A["calls"].append(int(s["llm_delta"]))
        if run.error and not skip:
            flagged = [s for s in steps if s.get("status") not in ("ok", "meta", None)]
            target = flagged[-1] if flagged else (steps[-1] if steps else None)
            if target:
                node = target.get("node") or "?"
                A = nodes.setdefault(node, _new_node())
                A["fail"] += 1
            err_label = _error_label(run.error)
            if err_label != "skipped":
                entry = errors.setdefault(err_label, {"count": 0, "example": ""})
                entry["count"] += 1
                entry["example"] = entry["example"] or run.error

    # event ingestion stats for the window
    event_q = db.query(UserEvent)
    if start is not None:
        event_q = event_q.filter(UserEvent.occurred_at >= start)
    window_events = event_q.all()
    event_mix: dict[str, int] = {}
    for e in window_events:
        event_mix[e.event_type] = event_mix.get(e.event_type, 0) + 1
    events_24h = (
        db.query(UserEvent)
        .filter(UserEvent.occurred_at >= now - timedelta(hours=24))
        .count()
    )

    # node chart payloads
    def _node_items(field: str, limit: int = 8):
        items = []
        for name, A in nodes.items():
            if field == "latency":
                if A["durations"]:
                    items.append((name, round(statistics.mean(A["durations"]))))
            elif field == "tokens":
                if A["tokens"]:
                    items.append((name, round(statistics.mean(A["tokens"]))))
            elif field == "fail":
                if A["fail"] + A["fallback"] > 0:
                    items.append((name, A["fail"] + A["fallback"]))
        items.sort(key=lambda t: t[1], reverse=True)
        return [{"name": k, "value": v} for k, v in items[:limit]]

    node_latency = _node_items("latency")
    node_tokens = _node_items("tokens")
    node_failures = _node_items("fail")

    error_items = sorted(
        errors.items(), key=lambda kv: kv[1]["count"], reverse=True
    )
    top_error = error_items[0] if error_items else None

    trigger_items = sorted(triggers.items(), key=lambda kv: kv[1], reverse=True)
    event_items = sorted(event_mix.items(), key=lambda kv: kv[1], reverse=True)

    total_runs = len(runs)
    successful = sum(1 for r in runs if r.error is None)
    failed = total_runs - skipped_runs - successful
    effective = successful + failed
    success_rate = round(successful / effective * 100) if effective else 0

    insights = []
    if total_runs:
        if skipped_runs:
            insights.append(
                f"{skipped_runs} of {total_runs} runs ({round(skipped_runs / total_runs * 100)}%) "
                f"were skipped on insufficient signal — no failure, no recommendation made."
            )
        insights.append(
            f"{success_rate}% of {effective} actionable runs succeeded in this window "
            f"(p95 latency {_percentile(durations, 0.95):,} ms)."
        )
        if durations:
            slowest = max(nodes.items(), key=lambda kv: statistics.mean(kv[1]["durations"]) if kv[1]["durations"] else 0, default=None)
            if slowest and slowest[1]["durations"]:
                insights.append(
                    f"Slowest step: {slowest[0]} averaging {statistics.mean(slowest[1]['durations']):.0f} ms/node."
                )
        if top_error:
            insights.append(f"Most common failure reason: \"{top_error[0]}\" ({top_error[1]['count']}x).")
        insights.append(
            f"Avg {round(statistics.mean(token_list), 0):,.0f} tokens / run — "
            f"estimated ${total_cost:.4f} in this window."
        )
        if fallback_runs:
            insights.append(f"{fallback_runs} runs ({(fallback_runs / total_runs * 100):.0f}%) used a fallback path.")
    else:
        insights.append("No agent runs in this window yet.")

    labels = [label(b) for b in starts]
    return {
        "range": range_key,
        "window_label": RANGE_LABELS.get(range_key, "All time"),
        "summary": {
            "total_runs": total_runs,
            "successful_runs": successful,
            "failed_runs": failed,
            "success_rate": success_rate,
            "avg_duration_ms": round(statistics.mean(durations)) if durations else 0,
            "p50_duration_ms": _percentile(durations, 0.5),
            "p95_duration_ms": _percentile(durations, 0.95),
            "avg_tokens": round(statistics.mean(token_list)) if token_list else 0,
            "avg_llm_calls": round(statistics.mean(call_list), 2) if call_list else 0,
            "est_cost_usd": round(total_cost, 4),
            "fallback_runs": fallback_runs,
            "skipped_runs": skipped_runs,
            "events_ingested": len(window_events),
            "events_24h": events_24h,
        },
        "series": {
            "runs": {"labels": labels, "ok": ok_series, "fail": fail_series, "skip": skip_series},
            "tokens": {"labels": labels, "counts": token_series},
            "cost": {"labels": labels, "counts": [round(c, 4) for c in cost_series]},
        },
        "nodes": {
            "latency": node_latency,
            "tokens": node_tokens,
            "failures": node_failures,
        },
        "error_mix": [
            {"name": k, "value": v["count"]} for k, v in error_items[:8]
        ],
        "trigger_mix": [
            {"name": k, "value": v} for k, v in trigger_items
        ],
        "event_mix": [
            {"name": k, "value": v} for k, v in event_items[:8]
        ],
        "insights": insights,
    }


# --------------------------------------------------------------------------- #
# single-run trace detail
# --------------------------------------------------------------------------- #
def estimate_run_cost(run: AgentRun) -> float:
    steps = run.steps if isinstance(run.steps, list) else []
    return _run_cost(steps, run.total_tokens)


def run_detail(db: Session, run_id: int) -> dict | None:
    run = db.query(AgentRun).get(run_id)
    if run is None:
        return None

    user = db.query(User).get(run.user_id) if run.user_id else None
    steps = run.steps if isinstance(run.steps, list) else []
    meta = next((s for s in steps if s.get("node") == "meta"), None)
    meta_extra = (meta.get("extra") or {}) if meta else {}

    event_ids = meta_extra.get("input_event_ids") or []
    events = []
    if event_ids:
        rows = (
            db.query(UserEvent)
            .filter(UserEvent.id.in_(list(event_ids)))
            .order_by(UserEvent.occurred_at.asc())
            .all()
        )
        events = [
            {
                "id": e.id,
                "type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "payload": e.payload or {},
                "occurred_at": e.occurred_at.isoformat(sep=" ", timespec="seconds"),
            }
            for e in rows
        ]

    annotated = []
    for s in steps:
        row = dict(s)
        if s.get("node") != "meta":
            row["model"] = _model_for_node(s.get("node", ""))
        annotated.append(row)

    return {
        "id": run.id,
        "trace_id": run.trace_id,
        "trigger": run.trigger,
        "user_id": run.user_id,
        "user_email": user.email if user else None,
        "user_context": {"age": user.age, "gender": user.gender} if user and (user.age or user.gender) else None,
        "llm_calls": run.llm_calls,
        "total_tokens": run.total_tokens,
        "duration_ms": run.duration_ms,
        "error": run.error,
        "created_at": run.created_at.isoformat(sep=" ", timespec="seconds"),
        "cost_usd": _run_cost(steps, run.total_tokens),
        "steps": annotated,
        "meta": meta_extra,
        "events": events,
    }