"""Unify AI Gateway routes with inference payload tables (one catalog row per route)."""

from __future__ import annotations

import re
from typing import Any

from app.config import get_settings
from app.services.analytics import ai_gateway_usage_rollup


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


_ROUTE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def gateway_route_slug_from_label(label: str) -> str:
    """HTTP ``model`` for gateway when label is a display name; keep slug form when already valid."""
    s = (label or "").strip().lower()
    if _ROUTE_SLUG_RE.fullmatch(s):
        return s
    return _slug(label)


def inference_table_to_route_name(table_short: str, suffix: str | None = None) -> str:
    """UC table short name → AI Gateway route ``model`` (strip one ``_payload`` suffix only)."""
    s = (table_short or "").strip()
    suf = (suffix if suffix is not None else get_settings().inference_table_name_suffix) or "_payload"
    suf = suf.strip()
    if suf and s.endswith(suf):
        s = s[: -len(suf)]
    return s.lower()


def inference_fqn_for_agent_key(agent_key: str) -> str | None:
    """Resolve catalog/URL keys (``gw:route`` or ``inf:fqn``) to a UC payload table FQN."""
    k = (agent_key or "").strip()
    if not k:
        return None
    if k.lower().startswith("inf:"):
        return k[4:].strip() or None
    route: str | None = None
    if k.lower().startswith("gw:"):
        route = k[3:].strip().lower()
    else:
        route = k.lower()
    if not route:
        return None
    from app.services.inference import inference_fqn_list

    fqns, _ = inference_fqn_list()
    for f in fqns:
        short = f.split(".")[-1] if f else ""
        if inference_table_to_route_name(short) == route:
            return f
    return None


def gateway_routes_from_inference_fqns(fqns: list[str]) -> list[dict[str, Any]]:
    """Canonical routes from discovered payload tables (matches Databricks endpoint names)."""
    suffix = get_settings().inference_table_name_suffix or "_payload"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in fqns:
        short = f.split(".")[-1]
        route = inference_table_to_route_name(short, suffix)
        if not route or route in seen:
            continue
        seen.add(route)
        out.append(
            {
                "route": route,
                "display_label": route,
                "fqn": f,
            },
        )
    return out


def gateway_route_slugs(hours: int = 24 * 14) -> list[dict[str, Any]]:
    """Optional usage stats keyed by route (display names in usage may differ from endpoint ``model``)."""
    gw = ai_gateway_usage_rollup(hours)
    if gw.get("error"):
        return []
    by_route: dict[str, dict[str, Any]] = {}
    for r in gw.get("by_model") or []:
        raw = str(r.get("model") or "").strip()
        if not raw or raw.lower() == "unknown":
            continue
        slug = gateway_route_slug_from_label(raw)
        if not slug:
            continue
        prev = by_route.get(slug)
        if prev:
            prev["requests"] = int(prev.get("requests") or 0) + int(r.get("requests") or 0)
            prev["total_tokens"] = int(prev.get("total_tokens") or 0) + int(r.get("total_tokens") or 0)
        else:
            by_route[slug] = {
                "route": slug,
                "display_label": raw,
                "requests": int(r.get("requests") or 0),
                "total_tokens": int(r.get("total_tokens") or 0),
            }
    return list(by_route.values())


def unified_agents_catalog(*, hours: int = 24 * 14) -> dict[str, Any]:
    """One row per payload table / gateway route — never duplicate inf + gw rows."""
    from app.services.inference import inference_fqn_list

    fqns, fq_note = inference_fqn_list()
    routes = gateway_routes_from_inference_fqns(fqns)
    usage = {r["route"]: r for r in gateway_route_slugs(hours)}
    items: list[dict[str, Any]] = []

    for r in routes:
        route = r["route"]
        u = usage.get(route) or {}
        # Match usage by fuzzy slug if display name differs (e.g. Gemma 3 12B → gemma-3-12b)
        if not u:
            for uslug, ud in usage.items():
                if route.replace("_", "-") in uslug or uslug.replace("-", "_") in route:
                    u = ud
                    break
        items.append(
            {
                "key": f"gw:{route}",
                "kind": "gateway_route",
                "label": r["display_label"],
                "gateway_model": route,
                "fqn": r["fqn"],
                "requests_preview": int(u.get("requests") or 0),
                "total_tokens_preview": int(u.get("total_tokens") or 0),
            },
        )

    if not items and usage:
        for slug, u in usage.items():
            items.append(
                {
                    "key": f"gw:{slug}",
                    "kind": "gateway_route",
                    "label": u.get("display_label") or slug,
                    "gateway_model": slug,
                    "fqn": None,
                    "requests_preview": int(u.get("requests") or 0),
                    "total_tokens_preview": int(u.get("total_tokens") or 0),
                },
            )

    return {
        "agents": items,
        "catalog_mode": "inference_tables" if routes else "usage_only",
        "payload_table_count": len(fqns),
        "gateway_distinct_models": len(items),
        "fqns_note": fq_note,
        "gateway_error": None,
        "gateway_hours_sampled": hours,
    }
