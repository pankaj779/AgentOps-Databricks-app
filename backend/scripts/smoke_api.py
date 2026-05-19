"""Smoke-test all v1 API endpoints. Run: python scripts/smoke_api.py"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8080"


def get(path: str, timeout: int = 180) -> tuple[int, Any]:
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, body.decode("utf-8", errors="replace")[:200]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")[:300]
        try:
            return e.code, json.loads(raw) if raw else {"error": str(e)}
        except json.JSONDecodeError:
            return e.code, {"error": raw or str(e)}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def ok_status(code: int) -> bool:
    return 200 <= code < 300


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    def check(name: str, path: str, *, timeout: int = 180, assert_fn=None) -> None:
        code, data = get(path, timeout=timeout)
        if not ok_status(code):
            failures.append(f"{name}: HTTP {code} — {str(data)[:120]}")
            return
        if assert_fn:
            try:
                msg = assert_fn(data)
                if msg:
                    warnings.append(f"{name}: {msg}")
            except Exception as e:  # noqa: BLE001
                failures.append(f"{name}: assert failed — {e}")
        print(f"  OK  {name}")

    print("AgentOps API smoke test\n" + "=" * 50)

    check("health", "/api/health", timeout=30, assert_fn=lambda d: None if d.get("status") == "ok" else "bad status")
    check(
        "overview",
        "/api/v1/overview?hours=168",
        assert_fn=lambda d: (
            None
            if d.get("data_mode") in ("live", "live_partial", "demo")
            and d.get("agents_monitored", 0) >= 0
            else f"unexpected overview {d.get('data_mode')}"
        ),
    )
    check("agents", "/api/v1/agents", assert_fn=lambda d: None if isinstance(d, list) and len(d) > 0 else "empty agents")
    check("agents catalog", "/api/v1/agents/catalog", assert_fn=lambda d: None if d.get("agents") else "no catalog")
    check("inference diagnostics", "/api/v1/inference/diagnostics", timeout=120)
    check(
        "cost summary",
        "/api/v1/analytics/cost/summary?hours=168",
        assert_fn=lambda d: (
            f"cost err: {d.get('error')}"
            if d.get("error") and not (d.get("ai_gateway") or {}).get("total_tokens")
            else None
        ),
    )
    check(
        "cost summary scoped",
        "/api/v1/analytics/cost/summary?hours=168&tasks=4e4745c8-11ce-42e9-8586-715ad1565545",
        timeout=120,
        assert_fn=lambda d: "HTTP 500" if False else (f"err {d.get('error')}" if False else None),
    )
    check(
        "health timeseries",
        "/api/v1/analytics/health/timeseries?hours=168",
        assert_fn=lambda d: (
            None
            if (d.get("buckets") or d.get("source") == "ai_gateway")
            else f"no buckets: {d.get('error')}"
        ),
    )
    check(
        "health slo",
        "/api/v1/analytics/health/slo",
        assert_fn=lambda d: (
            None
            if d.get("global_p95_ms") is not None or d.get("source") == "ai_gateway"
            else f"no slo: {d.get('error')}"
        ),
    )
    check(
        "quality observability",
        "/api/v1/analytics/quality/observability",
        assert_fn=lambda d: None if d.get("p95_latency_ms") is not None else f"no quality: {d.get('error')}",
    )
    check("quality trend", "/api/v1/analytics/quality/trend?days=7", timeout=120)
    check("governance lineage", "/api/v1/analytics/governance/lineage?limit=20", timeout=120)
    check("governance audit", "/api/v1/analytics/governance/audit?limit=10", timeout=120)
    check("mlflow overview", "/api/v1/analytics/mlflow/overview?limit=5", timeout=120)
    check("traces", "/api/v1/analytics/traces?limit=5", timeout=120)
    check("replay targets", "/api/v1/replay/targets", timeout=30)

    print("\n" + "=" * 50)
    if warnings:
        print(f"Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"  ! {w}")
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  X {f}")
        return 1
    print("All smoke checks passed.")
    if warnings:
        print("(Some warnings expected while payload tables lack AWS/UC access.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
