"""Agent comparison scorecard — routes + 7d stats + optional live benchmark."""

from __future__ import annotations

from typing import Any

from app.services.agent_unify import gateway_routes_from_inference_fqns
from app.services.analytics import ai_gateway_usage_rollup, health_slo_summary
from app.services.gateway_aliases import apply_aliases_to_gateway_rollup, canonical_route_name
from app.services.inference import inference_fqn_list
from app.services.replay import load_replay_targets


def build_compare_scorecard(*, hours: int = 168) -> dict[str, Any]:
    h = max(24, min(24 * 90, int(hours)))
    fqns, _ = inference_fqn_list()
    routes = gateway_routes_from_inference_fqns(fqns)
    targets = load_replay_targets()
    gw = apply_aliases_to_gateway_rollup(ai_gateway_usage_rollup(h))
    by_model = {canonical_route_name(r.get("model")): r for r in gw.get("by_model") or []}
    slo = health_slo_summary(p95_target_ms=2000, error_budget_pct=2.0)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in routes:
        route = str(r["route"])
        if route in seen:
            continue
        seen.add(route)
        g = by_model.get(route) or {}
        t = next((x for x in targets if (x.model or x.id).lower() == route), None)
        rows.append(
            {
                "route": route,
                "label": r.get("display_label") or route,
                "fqn": r.get("fqn"),
                "requests_7d": int(g.get("requests") or 0),
                "total_tokens_7d": int(g.get("total_tokens") or 0),
                "input_tokens_7d": int(g.get("input_tokens") or 0),
                "output_tokens_7d": int(g.get("output_tokens") or 0),
                "display_labels": g.get("display_labels") or (
                    [g["display_label"]] if g.get("display_label") else []
                ),
                "in_replay_targets": t is not None,
                "error_rate_pct": None,
            },
        )

    for t in targets:
        route = (t.model or t.id or "").lower()
        if route in seen:
            continue
        seen.add(route)
        g = by_model.get(route) or {}
        rows.append(
            {
                "route": route,
                "label": t.label or route,
                "fqn": None,
                "requests_7d": int(g.get("requests") or 0),
                "total_tokens_7d": int(g.get("total_tokens") or 0),
                "input_tokens_7d": int(g.get("input_tokens") or 0),
                "output_tokens_7d": int(g.get("output_tokens") or 0),
                "display_labels": [],
                "in_replay_targets": True,
                "error_rate_pct": None,
            },
        )

    rows.sort(key=lambda x: -(x.get("total_tokens_7d") or 0))
    return {
        "window_hours": h,
        "rows": rows,
        "gateway_error": gw.get("error"),
        "slo_note": slo.get("note"),
        "target_count": len(targets),
    }
