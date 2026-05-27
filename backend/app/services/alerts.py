"""Simple week-over-week change detection for overview alerts."""

from __future__ import annotations

from typing import Any

from app.services.gateway_aliases import canonical_route_name
from app.services.analytics import ai_gateway_usage_rollup, health_timeseries
from app.services.app_config_store import effective_exclude_test_requests
from app.services.inference import inference_metrics


def compute_change_alerts(*, hours: int = 168) -> dict[str, Any]:
    """Compare current window vs prior window of equal length."""
    h = max(24, min(24 * 14, int(hours)))
    alerts: list[dict[str, Any]] = []

    gw_now = ai_gateway_usage_rollup(h)
    gw_prev = ai_gateway_usage_rollup(h * 2)  # 2x window — we'll approximate prior half
    if not gw_now.get("error"):
        tok_now = int(gw_now.get("total_tokens") or 0)
        req_now = int(gw_now.get("total_requests") or 0)
        tok_all = int(gw_prev.get("total_tokens") or 0) if not gw_prev.get("error") else 0
        req_all = int(gw_prev.get("total_requests") or 0) if not gw_prev.get("error") else 0
        tok_prev = max(0, tok_all - tok_now)
        req_prev = max(0, req_all - req_now)
        if tok_prev > 0:
            pct = round(100.0 * (tok_now - tok_prev) / tok_prev, 1)
            if abs(pct) >= 15:
                alerts.append(
                    {
                        "severity": "warn" if pct > 40 else "info",
                        "title": f"Gateway tokens {pct:+.0f}% vs prior {h}h",
                        "detail": f"Now {tok_now:,} tokens ({req_now} reqs); prior period ~{tok_prev:,}.",
                        "metric": "gateway_tokens",
                    },
                )
        by_now = {canonical_route_name(r.get("model")): r for r in gw_now.get("by_model") or []}
        by_prev_raw = gw_prev.get("by_model") or []
        by_prev: dict[str, int] = {}
        for r in by_prev_raw:
            c = canonical_route_name(r.get("model"))
            by_prev[c] = by_prev.get(c, 0) + int(r.get("total_tokens") or 0)
        for c, r in by_now.items():
            t_now = int(r.get("total_tokens") or 0)
            t_prev = by_prev.get(c, 0)
            if t_prev > 10 and t_now > 0:
                pct_m = round(100.0 * (t_now - t_prev) / t_prev, 1)
                if abs(pct_m) >= 25:
                    alerts.append(
                        {
                            "severity": "warn" if pct_m > 50 else "info",
                            "title": f"{c}: tokens {pct_m:+.0f}%",
                            "detail": f"{t_now:,} tokens now vs ~{t_prev:,} in prior window.",
                            "metric": "model_tokens",
                            "model": c,
                        },
                    )

    inf = inference_metrics()
    if inf.get("count_24h") is not None and inf.get("count_7d") is not None:
        c24 = int(inf["count_24h"])
        c7 = int(inf["count_7d"])
        avg_prior = max(0, (c7 - c24) / 6.0) if c7 > c24 else 0
        if avg_prior > 0 and c24 > avg_prior * 1.5:
            alerts.append(
                {
                    "severity": "info",
                    "title": "Request volume up in last 24h",
                    "detail": f"{c24} requests (24h) vs ~{avg_prior:.0f}/day earlier in the week.",
                    "metric": "inference_requests",
                },
            )

    ts = health_timeseries(hours=48)
    if not ts.get("error") and (ts.get("buckets") or []):
        buckets = ts["buckets"][-48:]
        mid = len(buckets) // 2
        err_recent = sum(b.get("errors", 0) for b in buckets[mid:])
        err_prior = sum(b.get("errors", 0) for b in buckets[:mid])
        req_recent = sum(b.get("requests", 0) for b in buckets[mid:])
        if req_recent >= 3 and err_recent > err_prior + 1:
            alerts.append(
                {
                    "severity": "warn",
                    "title": "Error rate spike",
                    "detail": f"{err_recent} errors in recent {len(buckets) - mid}h vs {err_prior} in prior half.",
                    "metric": "errors",
                },
            )

    return {
        "alerts": alerts[:12],
        "window_hours": h,
        "exclude_test_requests": effective_exclude_test_requests(),
    }
