"""HTTP replay of a logged request against alternate model endpoints (local compare)."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.analytics import trace_detail


class ReplayTargetSpec(BaseModel):
    id: str
    label: str
    url: str
    timeout_sec: float = Field(default=120.0, ge=1.0, le=600.0)
    headers: dict[str, str] = Field(default_factory=dict)


def load_replay_targets() -> list[ReplayTargetSpec]:
    raw = get_settings().replay_targets_json.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[ReplayTargetSpec] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ReplayTargetSpec(**item))
        except Exception:
            continue
    return out


def replay_targets_public() -> dict[str, Any]:
    return {"targets": [{"id": t.id, "label": t.label} for t in load_replay_targets()]}


def _usage_from_response_body(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    inp = usage.get("prompt_tokens")
    if inp is None:
        inp = usage.get("input_tokens")
    out_t = usage.get("completion_tokens")
    if out_t is None:
        out_t = usage.get("output_tokens")
    tot = usage.get("total_tokens")
    return {
        "input_tokens": inp,
        "output_tokens": out_t,
        "total_tokens": tot,
    }


def run_replay(request_id: str, target_ids: list[str] | None = None) -> dict[str, Any]:
    """POST logged `request_json` to each configured target; return timings + parsed usage."""
    detail = trace_detail(request_id)
    if detail.get("error"):
        return {"error": detail["error"], "request_id": request_id, "results": []}

    body = detail.get("request_json")
    if body is None:
        rec = detail.get("record") or {}
        req_raw = rec.get("request")
        if isinstance(req_raw, str):
            try:
                body = json.loads(req_raw)
            except json.JSONDecodeError:
                body = None
    if body is None:
        return {"error": "no_request_json_for_replay", "request_id": request_id, "results": []}

    targets = load_replay_targets()
    if target_ids:
        want = set(target_ids)
        targets = [t for t in targets if t.id in want]
    if not targets:
        return {"error": "no_replay_targets_configured", "request_id": request_id, "results": []}

    results: list[dict[str, Any]] = []
    with httpx.Client(follow_redirects=True) as client:
        for t in targets:
            headers = {"Content-Type": "application/json", **t.headers}
            t0 = time.monotonic()
            try:
                r = client.post(t.url, json=body, headers=headers, timeout=t.timeout_sec)
                dt_ms = (time.monotonic() - t0) * 1000.0
                usage = _usage_from_response_body(r.text)
                err_s: str | None = None
                if not r.is_success:
                    err_s = (r.text or r.reason_phrase or "HTTP error")[:800]
                results.append(
                    {
                        "target_id": t.id,
                        "label": t.label,
                        "status_code": r.status_code,
                        "latency_ms": round(dt_ms, 2),
                        "usage": usage,
                        "error": err_s,
                    },
                )
            except Exception as e:  # noqa: BLE001
                dt_ms = (time.monotonic() - t0) * 1000.0
                results.append(
                    {
                        "target_id": t.id,
                        "label": t.label,
                        "status_code": None,
                        "latency_ms": round(dt_ms, 2),
                        "usage": None,
                        "error": str(e).strip()[:800],
                    },
                )

    return {"request_id": request_id, "results": results, "error": None}
