"""Shared helpers for replay/benchmark: parse responses, cost estimates, test-run stamping."""

from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Sequence

import httpx

from app.config import get_settings
from app.services.analytics import ai_gateway_usage_rollup, billing_model_serving_cost

# OpenAI-style chat `user` field — supported by AI Gateway; NOT top-level `metadata` (rejected with 400).
AGENTOPS_USER_HIDE = "agentops_dash_test_hide"
AGENTOPS_USER_SHOW = "agentops_dash_test_show"
BENCHMARK_MODEL_SENTINEL = "agentops-benchmark"
HEADER_AGENTOPS_TEST = "X-AgentOps-Test"
HEADER_AGENTOPS_TRACK = "X-AgentOps-Track-Dashboard"

_COST_RATE_CACHE: tuple[float | None, float, str] | None = None  # (usd_per_token, cached_at, source)


def apply_tracking_stamps(payload: dict[str, Any], *, track_in_dashboard: bool) -> dict[str, Any]:
    """Tag outbound gateway JSON so dashboard SQL can exclude casual test traffic."""
    p = copy.deepcopy(payload)
    # Databricks AI Gateway rejects unknown top-level fields (e.g. metadata).
    p.pop("metadata", None)
    p["user"] = AGENTOPS_USER_SHOW if track_in_dashboard else AGENTOPS_USER_HIDE
    return p


def tracking_headers(track_in_dashboard: bool) -> dict[str, str]:
    return {
        HEADER_AGENTOPS_TEST: "1",
        HEADER_AGENTOPS_TRACK: "1" if track_in_dashboard else "0",
    }


def test_request_sql_exclude_fragment(cols: list[str]) -> str:
    """Exclude AgentOps replay/benchmark rows. Uses positive match only so NULL/empty request bodies stay visible."""
    from app.services.app_config_store import effective_exclude_test_requests

    if not effective_exclude_test_requests():
        return ""
    cmap = {c.lower(): c for c in cols}
    rq = cmap.get("request")
    if not rq:
        return ""
    hide = AGENTOPS_USER_HIDE.replace("'", "''")
    bench = BENCHMARK_MODEL_SENTINEL.replace("'", "''")
    body = f"COALESCE(CAST(`{rq}` AS STRING), '')"
    return (
        f" AND NOT ("
        f"{body} LIKE '%{hide}%' "
        f"OR {body} LIKE '%\"model\":\"{bench}\"%' "
        f"OR {body} LIKE '%\"model\": \"{bench}\"%'"
        f") "
    )


def fetch_test_request_ids(hours: int = 168, *, limit: int = 2000) -> list[str]:
    """Gateway request_ids to drop from workspace rollups when excluding dashboard tests."""
    from app.services.app_config_store import effective_exclude_test_requests

    if not effective_exclude_test_requests():
        return []
    from app.services.analytics import _inference_table_ctx  # noqa: PLC0415

    ctx, err = _inference_table_ctx()
    if err or not ctx:
        return []
    cmap = {c.lower(): c for c in ctx["cols"]}
    rq = cmap.get("request")
    rqid = cmap.get("request_id")
    if not rq or not rqid:
        return []
    hide = AGENTOPS_USER_HIDE.replace("'", "''")
    bench = BENCHMARK_MODEL_SENTINEL.replace("'", "''")
    body = f"COALESCE(CAST(`{rq}` AS STRING), '')"
    tc = ctx["time_col"]
    tbl = ctx["table_sql"]
    h = max(1, min(24 * 90, int(hours)))
    lim = max(1, min(5000, int(limit)))
    sql = (
        f"SELECT DISTINCT CAST(`{rqid}` AS STRING) AS rid FROM {tbl} "
        f"WHERE `{tc}` >= current_timestamp() - INTERVAL {h} HOURS "
        f"AND (`{rqid}` IS NOT NULL) "
        f"AND ("
        f"{body} LIKE '%{hide}%' "
        f"OR {body} LIKE '%\"model\":\"{bench}\"%' "
        f"OR {body} LIKE '%\"model\": \"{bench}\"%'"
        f") "
        f"LIMIT {lim}"
    )
    try:
        from app.databricks.sql_client import sql_connection  # noqa: PLC0415

        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
        out: list[str] = []
        for row in rows:
            if row and row[0] is not None:
                s = str(row[0]).strip()
                if s:
                    out.append(s)
        return out
    except Exception:  # noqa: BLE001
        return []


def gateway_exclude_test_request_ids_clause(hours: int) -> str:
    """AND fragment for system.ai_gateway.usage (skip when querying explicit pinned request_ids)."""
    ids = fetch_test_request_ids(hours)
    if not ids:
        return ""
    in_list = ",".join("'" + i.replace("'", "''") + "'" for i in ids)
    return f" AND request_id NOT IN ({in_list}) "


def extract_question_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not payload or not isinstance(payload, dict):
        return None
    msgs = payload.get("messages")
    if not isinstance(msgs, list):
        return None
    parts: list[str] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(f"[{role}] {content.strip()}")
        elif content is not None:
            parts.append(f"[{role}] {json.dumps(content, default=str)[:2000]}")
    return "\n\n".join(parts) if parts else None


def parse_chat_completion(text: str) -> dict[str, Any]:
    """Parse OpenAI-style chat completion body into usage, answer, model."""
    out: dict[str, Any] = {
        "usage": None,
        "answer": None,
        "model": None,
        "response_preview": (text or "")[:400].replace("\n", " "),
        "parse_error": None,
    }
    if not text or not text.strip():
        return out
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        out["parse_error"] = str(e)[:200]
        out["answer"] = text[:12000]
        return out
    if not isinstance(data, dict):
        out["answer"] = text[:12000]
        return out
    out["model"] = data.get("model")
    usage = data.get("usage")
    if isinstance(usage, dict):
        inp = usage.get("prompt_tokens")
        if inp is None:
            inp = usage.get("input_tokens")
        out_t = usage.get("completion_tokens")
        if out_t is None:
            out_t = usage.get("output_tokens")
        out["usage"] = {
            "input_tokens": inp,
            "output_tokens": out_t,
            "total_tokens": usage.get("total_tokens"),
        }
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0]
        if isinstance(ch0, dict):
            msg = ch0.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    out["answer"] = content
                elif content is not None:
                    out["answer"] = json.dumps(content, default=str)
            elif ch0.get("text"):
                out["answer"] = str(ch0.get("text"))
    if not out["answer"]:
        out["answer"] = text[:12000]
    return out


def _usd_per_token_cached() -> tuple[float | None, str]:
    """Compare UI: prefer explicit AGENTOPS_COMPARE_USD_PER_1M_TOKENS, else 24h billing average."""
    global _COST_RATE_CACHE
    now = time.monotonic()
    if _COST_RATE_CACHE and now - _COST_RATE_CACHE[1] < 300:
        return _COST_RATE_CACHE[0], _COST_RATE_CACHE[2]
    rate: float | None = None
    source = "unavailable"
    fallback = get_settings().compare_fallback_usd_per_1m_tokens
    if fallback > 0:
        rate = float(fallback) / 1_000_000.0
        source = f"env_{fallback}_usd_per_1m_tokens"
    else:
        try:
            gw = ai_gateway_usage_rollup(24)
            bill = billing_model_serving_cost(24)
            tt = int(gw.get("total_tokens") or 0) if not gw.get("error") else 0
            usd = bill.get("total_list_usd")
            if tt > 0 and isinstance(usd, (int, float)) and float(usd) > 0:
                rate = float(usd) / float(tt)
                source = "prorated_from_billing_24h"
        except Exception:  # noqa: BLE001
            pass
    _COST_RATE_CACHE = (rate, now, source)
    return rate, source


def cost_estimate_meta() -> dict[str, Any]:
    rate, source = _usd_per_token_cached()
    per_1m = round(rate * 1_000_000.0, 4) if rate else None
    return {
        "usd_per_1m_tokens": per_1m,
        "cost_source": source,
        "note": (
            f"Est. list $ = tokens × (${per_1m}/1M) from {source}. "
            "Not an invoice; per-row values differ by token count."
            if per_1m is not None
            else "Cost estimate unavailable — set AGENTOPS_COMPARE_USD_PER_1M_TOKENS in backend/.env"
        ),
    }


def execute_all_targets(
    targets: Sequence[Any],
    *,
    base_payload: dict[str, Any],
    track_in_dashboard: bool,
    merge_auth_headers: Callable[..., dict[str, str]],
    payload_for_target: Callable[[dict[str, Any], Any], dict[str, Any]],
) -> list[dict[str, Any]]:
    """POST to each target in parallel (faster; avoids dev-proxy timeouts)."""
    stamped = apply_tracking_stamps(base_payload, track_in_dashboard=track_in_dashboard)
    ordered: list[dict[str, Any] | None] = [None] * len(targets)

    def _one(idx: int, t: Any) -> tuple[int, dict[str, Any]]:
        headers = merge_auth_headers(t.headers, track_in_dashboard=track_in_dashboard)
        payload = payload_for_target(stamped, t)
        t0 = time.monotonic()
        try:
            with httpx.Client(follow_redirects=True) as client:
                r = client.post(t.url, json=payload, headers=headers, timeout=t.timeout_sec)
            dt_ms = (time.monotonic() - t0) * 1000.0
            err_s: str | None = None
            if not r.is_success:
                err_s = (r.text or r.reason_phrase or "HTTP error")[:800]
            hint_401 = None
            if r.status_code == 401:
                hint_401 = "401: PAT missing ai-gateway scope or API not restarted after .env change."
            row = enrich_compare_row(
                target_id=t.id,
                label=t.label,
                status_code=r.status_code,
                latency_ms=dt_ms,
                response_text=r.text or "",
                error=err_s,
                model=t.model,
                hint=hint_401,
            )
            return idx, row
        except Exception as e:  # noqa: BLE001
            dt_ms = (time.monotonic() - t0) * 1000.0
            return idx, enrich_compare_row(
                target_id=t.id,
                label=t.label,
                status_code=None,
                latency_ms=dt_ms,
                response_text="",
                error=str(e).strip()[:800],
                model=t.model,
            )

    workers = max(1, min(6, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, i, t) for i, t in enumerate(targets)]
        for fut in as_completed(futs):
            idx, row = fut.result()
            ordered[idx] = row
    return [r for r in ordered if r is not None]


def estimate_list_usd(
    total_tokens: int | None,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    del model  # reserved for per-model SKU later
    if total_tokens is None:
        return {"est_list_usd": None, "cost_source": "no_tokens"}
    try:
        tok = int(total_tokens)
    except (TypeError, ValueError):
        return {"est_list_usd": None, "cost_source": "invalid_tokens"}
    if tok <= 0:
        return {"est_list_usd": 0.0, "cost_source": "zero_tokens"}
    rate, source = _usd_per_token_cached()
    if rate is None or rate <= 0:
        return {"est_list_usd": None, "cost_source": source}
    return {"est_list_usd": round(tok * rate, 6), "cost_source": source}


def enrich_compare_row(
    *,
    target_id: str,
    label: str,
    status_code: int | None,
    latency_ms: float,
    response_text: str,
    error: str | None,
    model: str | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    parsed = parse_chat_completion(response_text or "")
    usage = parsed.get("usage")
    tot = None
    if isinstance(usage, dict) and usage.get("total_tokens") is not None:
        try:
            tot = int(usage["total_tokens"])
        except (TypeError, ValueError):
            tot = None
    cost = estimate_list_usd(tot, model=model or parsed.get("model"))
    return {
        "target_id": target_id,
        "label": label,
        "status_code": status_code,
        "latency_ms": round(latency_ms, 2),
        "usage": usage,
        "model": model or parsed.get("model"),
        "answer": parsed.get("answer"),
        "response_preview": parsed.get("response_preview"),
        "error": error,
        "hint": hint,
        "est_list_usd": cost.get("est_list_usd"),
        "cost_source": cost.get("cost_source"),
    }


def compute_objective_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [
        r
        for r in results
        if r.get("status_code") == 200 and not r.get("error") and r.get("target_id")
    ]
    if not ok:
        return {
            "fastest_target_id": None,
            "cheapest_target_id": None,
            "fewest_tokens_target_id": None,
            "note": "No successful rows to rank.",
        }

    def _lat(r: dict[str, Any]) -> float:
        try:
            return float(r.get("latency_ms") or 1e18)
        except (TypeError, ValueError):
            return 1e18

    def _tok(r: dict[str, Any]) -> float:
        u = r.get("usage") or {}
        try:
            return float(u.get("total_tokens") or 1e18)
        except (TypeError, ValueError):
            return 1e18

    def _usd(r: dict[str, Any]) -> float:
        try:
            v = r.get("est_list_usd")
            return float(v) if v is not None else 1e18
        except (TypeError, ValueError):
            return 1e18

    fastest = min(ok, key=_lat)
    fewest = min(ok, key=_tok)
    with_cost = [r for r in ok if r.get("est_list_usd") is not None]
    cheapest = min(with_cost, key=_usd) if with_cost else None
    return {
        "fastest_target_id": fastest.get("target_id"),
        "cheapest_target_id": cheapest.get("target_id") if cheapest else None,
        "fewest_tokens_target_id": fewest.get("target_id"),
        "note": (
            "Automatic ranks use latency, tokens, and estimated list price only. "
            "Use Best/Worst after reading answers for quality."
        ),
    }
