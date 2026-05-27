"""Map AI Gateway usage display names to canonical route names (replay / agents catalog)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import BACKEND_ROOT, get_settings

_alias_cache: tuple[float, dict[str, str]] | None = None
_ALIAS_TTL = 60.0


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def load_gateway_aliases() -> dict[str, str]:
    """display_or_route_label → canonical route (lowercase)."""
    import time

    global _alias_cache
    now = time.monotonic()
    if _alias_cache and (now - _alias_cache[0]) < _ALIAS_TTL:
        return _alias_cache[1]

    s = get_settings()
    raw: list[dict[str, Any]] = []
    name = (s.gateway_aliases_file or "gateway_aliases.json").strip() or "gateway_aliases.json"
    for root in (BACKEND_ROOT, BACKEND_ROOT / "app"):
        p = root / name
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    raw = data
                elif isinstance(data, dict) and isinstance(data.get("aliases"), list):
                    raw = data["aliases"]
            except (OSError, json.JSONDecodeError):
                pass
            break

    out: dict[str, str] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        disp = str(row.get("display") or row.get("gateway_label") or "").strip()
        route = str(row.get("route") or row.get("model") or "").strip().lower()
        if disp and route:
            out[disp] = route
            out[disp.lower()] = route
            out[_slug(disp)] = route
        if route:
            out[route] = route
    _alias_cache = (now, out)
    return out


def canonical_route_name(label: str | None) -> str:
    """Resolve usage table model label to canonical route."""
    if not label or not str(label).strip():
        return "unknown"
    key = str(label).strip()
    aliases = load_gateway_aliases()
    if key in aliases:
        return aliases[key]
    kl = key.lower()
    if kl in aliases:
        return aliases[kl]
    sk = _slug(key)
    if sk in aliases:
        return aliases[sk]
    if _ROUTE_RE.fullmatch(kl):
        return kl
    return sk or kl


_ROUTE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def apply_aliases_to_gateway_rollup(rollup: dict[str, Any]) -> dict[str, Any]:
    """Merge by_model rows that map to the same canonical route."""
    if rollup.get("error") or not rollup.get("by_model"):
        return rollup
    merged: dict[str, dict[str, Any]] = {}
    for r in rollup.get("by_model") or []:
        raw = str(r.get("model") or "unknown")
        canon = canonical_route_name(raw)
        prev = merged.get(canon)
        if prev:
            prev["requests"] = int(prev.get("requests") or 0) + int(r.get("requests") or 0)
            prev["input_tokens"] = int(prev.get("input_tokens") or 0) + int(r.get("input_tokens") or 0)
            prev["output_tokens"] = int(prev.get("output_tokens") or 0) + int(r.get("output_tokens") or 0)
            prev["total_tokens"] = int(prev.get("total_tokens") or 0) + int(r.get("total_tokens") or 0)
            if raw != canon and raw not in (prev.get("display_labels") or []):
                prev.setdefault("display_labels", []).append(raw)
        else:
            merged[canon] = {
                "model": canon,
                "display_label": raw if raw != canon else None,
                "display_labels": [raw] if raw != canon else [],
                "requests": int(r.get("requests") or 0),
                "input_tokens": int(r.get("input_tokens") or 0),
                "output_tokens": int(r.get("output_tokens") or 0),
                "total_tokens": int(r.get("total_tokens") or 0),
            }
    out = dict(rollup)
    out["by_model"] = sorted(merged.values(), key=lambda x: -(x.get("total_tokens") or 0))
    out["aliases_applied"] = True
    return out
