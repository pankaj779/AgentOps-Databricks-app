"""Merged view of configured inference payload tables + AI Gateway models for drill-down UIs."""

from __future__ import annotations

from typing import Any

from app.services.analytics import ai_gateway_usage_rollup
from app.services.inference import inference_fqn_list


def agents_catalog() -> dict[str, Any]:
    """Stable keys for URL selection: inf:<UC FQN>, gw:<gateway model label>."""
    fqns, fq_note = inference_fqn_list()
    gw = ai_gateway_usage_rollup(24 * 14)
    items: list[dict[str, Any]] = []
    for f in fqns:
        short = f.split(".")[-1]
        items.append(
            {
                "key": f"inf:{f}",
                "kind": "inference_table",
                "label": short,
                "fqn": f,
            },
        )
    for r in gw.get("by_model") or []:
        m = str(r.get("model") or "unknown")
        items.append(
            {
                "key": f"gw:{m}",
                "kind": "ai_gateway",
                "label": m,
                "gateway_model": m,
                "requests_preview": int(r.get("requests") or 0),
                "total_tokens_preview": int(r.get("total_tokens") or 0),
            },
        )
    return {
        "agents": items,
        "payload_table_count": len(fqns),
        "gateway_distinct_models": len(gw.get("by_model") or []),
        "fqns_note": fq_note,
        "gateway_error": gw.get("error"),
        "gateway_hours_sampled": gw.get("hours"),
    }
