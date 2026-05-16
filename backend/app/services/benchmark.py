"""Fan-out the same chat prompt to each configured replay target (OpenAI-style JSON)."""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.replay import load_replay_targets, _usage_from_response_body


class BenchmarkPromptBody(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    max_tokens: int = Field(default=256, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    target_ids: list[str] | None = Field(default=None, description="Subset of replay target ids")


def run_prompt_benchmark(body: BenchmarkPromptBody) -> dict[str, Any]:
    s = get_settings()
    if not s.benchmark_enabled:
        return {
            "error": "benchmark_disabled",
            "hint": "Set AGENTOPS_BENCHMARK_ENABLED=true after configuring AGENTOPS_REPLAY_TARGETS_JSON.",
            "results": [],
        }
    targets = load_replay_targets()
    if body.target_ids:
        want = set(body.target_ids)
        targets = [t for t in targets if t.id in want]
    if len(targets) > s.benchmark_max_targets:
        targets = targets[: s.benchmark_max_targets]
    if not targets:
        return {
            "error": "no_replay_targets",
            "hint": "Add URLs in AGENTOPS_REPLAY_TARGETS_JSON (same schema as replay).",
            "results": [],
        }

    payload: dict[str, Any] = {
        "model": "agentops-benchmark",
        "messages": body.messages,
        "max_tokens": body.max_tokens,
        "temperature": body.temperature,
    }
    results: list[dict[str, Any]] = []
    with httpx.Client(follow_redirects=True) as client:
        for t in targets:
            headers = {"Content-Type": "application/json", **t.headers}
            t0 = time.monotonic()
            try:
                r = client.post(t.url, json=payload, headers=headers, timeout=t.timeout_sec)
                dt_ms = (time.monotonic() - t0) * 1000.0
                usage = _usage_from_response_body(r.text)
                err_s: str | None = None
                if not r.is_success:
                    err_s = (r.text or r.reason_phrase or "HTTP error")[:800]
                preview = (r.text or "")[:400].replace("\n", " ") if r.is_success else None
                results.append(
                    {
                        "target_id": t.id,
                        "label": t.label,
                        "status_code": r.status_code,
                        "latency_ms": round(dt_ms, 2),
                        "usage": usage,
                        "response_preview": preview,
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
                        "response_preview": None,
                        "error": str(e).strip()[:800],
                    },
                )

    return {"error": None, "results": results, "note": "Same JSON body sent to each target; compare usage + latency."}
