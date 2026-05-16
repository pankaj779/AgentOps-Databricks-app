"""Time-series, cost proxies, traces, and governance queries over inference + system tables."""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from typing import Any

from app.config import get_settings
from app.databricks.fqn import quote_fqn
from app.databricks.sql_client import sql_connection
from app.services.inference import (
    describe_columns,
    inference_agent_rollups,
    inference_fqn_list,
    inference_query_table_sql,
    resolve_source_table_fqn,
    resolve_time_column,
)
from app.services.inference import _pick_column as pick_col
from app.services.inference import LATENCY_CANDIDATES, STATUS_CANDIDATES

_CTX_ERR = tuple[None, str]
_CTX_OK = tuple[dict[str, Any], None]

_INFERENCE_CTX_TTL_SEC = 45.0
_inference_ctx_cache: dict[str, tuple[float, dict[str, Any] | None, str | None]] = {}


def _ctx_source_predicate(ctx: dict[str, Any], source_table: str | None) -> str:
    """Safe AND-clause fragment when filtering the inference union to one payload table."""
    if not source_table or not str(source_table).strip():
        return ""
    fqn = resolve_source_table_fqn(str(source_table).strip())
    if not fqn:
        return " AND 1=0 "
    esc = fqn.replace("'", "''")
    fqns = list(ctx.get("fqns") or [ctx.get("fqn")])
    if len(fqns) > 1:
        return (
            f" AND LOWER(TRIM(CAST(`_agentops_source_table` AS STRING))) = LOWER(TRIM('{esc}')) "
        )
    sole = fqns[0] if fqns else ""
    if sole and str(sole).lower() == fqn.lower():
        return ""
    return " AND 1=0 "


def _ctx_source_predicate_multi(ctx: dict[str, Any], source_tables: list[str] | None) -> str:
    """OR together payload-table filters for union inference streams."""
    if not source_tables:
        return ""
    resolved: list[str] = []
    for st in source_tables:
        fqn = resolve_source_table_fqn(str(st).strip())
        if not fqn:
            continue
        if fqn not in resolved:
            resolved.append(fqn)
    if not resolved:
        return " AND 1=0 "
    fqns_ctx_raw = ctx.get("fqns") or [ctx.get("fqn")]
    fqns_ctx = [str(x) for x in fqns_ctx_raw if x]
    allowed = {str(x).lower() for x in fqns_ctx}
    if len(fqns_ctx) > 1:
        ors: list[str] = []
        for fqn in resolved:
            if fqn.lower() not in allowed:
                continue
            esc = fqn.replace("'", "''")
            ors.append(
                f"LOWER(TRIM(CAST(`_agentops_source_table` AS STRING))) = LOWER(TRIM('{esc}'))"
            )
        if not ors:
            return " AND 1=0 "
        return " AND (" + " OR ".join(ors) + ") "
    sole = fqns_ctx[0] if fqns_ctx else ""
    uniq_lower = {r.lower() for r in resolved}
    if len(uniq_lower) > 1:
        return " AND 1=0 "
    only = resolved[0]
    if sole.lower() == only.lower():
        return ""
    return " AND 1=0 "


def _json_safe_value(v: Any) -> Any:
    """Convert Databricks SQL row values to JSON-serializable types for FastAPI."""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_json_safe_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe_value(val) for k, val in v.items()}
    return str(v)


def _ai_gateway_workspace_sql() -> str:
    """Optional workspace scope for system.ai_gateway.usage (digits only)."""
    ws = get_settings().workspace_id.strip()
    if ws.isdigit():
        return f" AND workspace_id = {int(ws)} "
    return ""


def ai_gateway_token_snapshots() -> dict[str, Any]:
    """24h / 7d total token sums for overview cards (system.ai_gateway.usage)."""
    w = _ai_gateway_workspace_sql()
    out: dict[str, Any] = {
        "total_tokens_24h": None,
        "total_tokens_7d": None,
        "error": None,
    }
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM system.ai_gateway.usage "
                f"WHERE event_time >= current_timestamp() - INTERVAL 24 HOURS{w}"
            )
            r24 = cur.fetchone()
            cur.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM system.ai_gateway.usage "
                f"WHERE event_time >= current_timestamp() - INTERVAL 7 DAYS{w}"
            )
            r7 = cur.fetchone()
        if r24 and r24[0] is not None:
            out["total_tokens_24h"] = int(r24[0])
        if r7 and r7[0] is not None:
            out["total_tokens_7d"] = int(r7[0])
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def _fetch_ai_gateway_usage_row(request_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """One row from system.ai_gateway.usage for this request_id (if logged there)."""
    rid = (request_id or "").strip().replace("'", "''")
    if not rid:
        return None, None
    w = _ai_gateway_workspace_sql()
    sql = (
        "SELECT request_id, event_time, latency_ms, status_code, "
        "CAST(destination_id AS STRING) AS destination_id, destination_model, destination_name, "
        "input_tokens, output_tokens, total_tokens, url, requester, api_type "
        "FROM system.ai_gateway.usage WHERE request_id = "
        f"'{rid}'{w}"
        "ORDER BY event_time DESC LIMIT 1"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
            colnames = [c[0] for c in cur.description] if cur.description else []
        if not row:
            return None, None
        rec = {colnames[i]: row[i] for i in range(len(colnames))}
        return {str(k): _json_safe_value(v) for k, v in rec.items()}, None
    except Exception as e:  # noqa: BLE001
        return None, str(e).strip()[:500]


def _fetch_ai_gateway_usage_batch(request_ids: list[str], *, limit: int = 64) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Latest row per request_id from system.ai_gateway.usage (deduped)."""
    uniq: list[str] = []
    seen: set[str] = set()
    for r in request_ids:
        rid = str(r or "").strip()
        if not rid or rid in seen:
            continue
        if not re.match(r"^[a-zA-Z0-9_\-\.]+$", rid):
            continue
        seen.add(rid)
        uniq.append(rid)
        if len(uniq) >= int(limit):
            break
    if not uniq:
        return {}, None
    in_list = ",".join("'" + u.replace("'", "''") + "'" for u in uniq)
    w = _ai_gateway_workspace_sql().strip()
    where_core = f"request_id IN ({in_list})"
    if w:
        where_core += f" {w}"
    sql = (
        "SELECT request_id, event_time, latency_ms, status_code, "
        "CAST(destination_id AS STRING) AS destination_id, destination_model, destination_name, "
        "input_tokens, output_tokens, total_tokens, url, requester, api_type "
        "FROM ("
        "SELECT *, ROW_NUMBER() OVER (PARTITION BY request_id ORDER BY event_time DESC) AS rn "
        f"FROM system.ai_gateway.usage WHERE {where_core}"
        ") sub WHERE rn = 1"
    )
    out_map: dict[str, dict[str, Any]] = {}
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
            colnames = [c[0] for c in cur.description] if cur.description else []
        for row in rows:
            rec = {colnames[i]: row[i] for i in range(len(colnames))}
            rec_j = {str(k): _json_safe_value(v) for k, v in rec.items()}
            rid_k = str(rec_j.get("request_id") or "")
            if rid_k:
                out_map[rid_k] = rec_j
        return out_map, None
    except Exception as e:  # noqa: BLE001
        return out_map, str(e).strip()[:500]


def _comparison_sql_column(cols: list[str], configured_logical: str) -> str | None:
    want = (configured_logical or "").strip().lower()
    if not want:
        return None
    cmap = {c.lower(): c for c in cols}
    return cmap.get(want)


def _sanitize_comparison_group_id(gid: str) -> tuple[str | None, str | None]:
    g = (gid or "").strip()
    if not g or not re.match(r"^[a-zA-Z0-9_\-\.:]{1,256}$", g):
        return None, "invalid group_id"
    return g, None


def billing_model_serving_between(start_ts: Any, end_ts: Any) -> dict[str, Any]:
    """MODEL_SERVING TOKEN billing rows between two timestamps (list-price USD)."""
    ws = get_settings().workspace_id.strip()
    out: dict[str, Any] = {
        "total_dbu": None,
        "total_list_usd": None,
        "currency_code": "USD",
        "by_endpoint": [],
        "pricing_partial": False,
        "error": None,
        "window_start": None,
        "window_end": None,
        "note": (
            "Estimate for [min_event_time, max_event_time] of the comparison group. "
            "Proportional per-request USD splits gateway-measured tokens vs sum of tokens in the group."
        ),
    }
    if not ws.isdigit():
        out["error"] = "set DATABRICKS_WORKSPACE_ID (digits) for billing.usage"
        return out
    def _lit(v: Any) -> str | None:
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            s = v.isoformat()
        else:
            s = str(v).strip()
        if not s:
            return None
        return s.replace("'", "''")[:80]

    e0 = _lit(start_ts)
    e1 = _lit(end_ts)
    if not e0 or not e1:
        out["error"] = "missing_timestamps"
        return out
    out["window_start"] = e0
    out["window_end"] = e1
    wpred = f"workspace_id = {int(ws)}"
    sql_agg = (
        "SELECT sku_name, "
        "COALESCE(NULLIF(TRIM(CAST(usage_metadata.endpoint_name AS STRING)), ''), "
        "'(none)') AS endpoint_name, "
        "CAST(SUM(usage_quantity) AS DOUBLE) AS dbu "
        "FROM system.billing.usage "
        f"WHERE {wpred} "
        f"AND usage_start_time >= CAST('{e0}' AS TIMESTAMP) "
        f"AND usage_start_time <= CAST('{e1}' AS TIMESTAMP) "
        "AND usage_type = 'TOKEN' "
        "AND billing_origin_product = 'MODEL_SERVING' "
        "GROUP BY 1, 2 "
        "ORDER BY dbu DESC NULLS LAST"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_agg)
            agg_rows = cur.fetchall() or []
        if not agg_rows:
            out["total_dbu"] = 0.0
            out["total_list_usd"] = 0.0
            out["by_endpoint"] = []
            return out

        skus: list[str] = []
        for r in agg_rows:
            if r and r[0] is not None:
                skus.append(str(r[0]))
        uniq = sorted(set(skus))
        escaped = ",".join("'" + s.replace("'", "''") + "'" for s in uniq)
        sql_prices = f"SELECT sku_name, currency_code, pricing FROM system.billing.list_prices WHERE sku_name IN ({escaped})"
        price_map: dict[str, tuple[float | None, str]] = {}
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_prices)
            for prow in cur.fetchall() or []:
                if not prow:
                    continue
                sku = str(prow[0])
                ccy = str(prow[1] or "USD")
                usd = _usd_per_dbu_from_pricing_json(prow[2])
                price_map[sku] = (usd, ccy)

        by_ep: list[dict[str, Any]] = []
        total_dbu = 0.0
        total_usd = 0.0
        for r in agg_rows:
            sku = str(r[0]) if r[0] else "unknown"
            ep = str(r[1]) if r[1] else "unknown"
            dbu = float(r[2] or 0.0)
            total_dbu += dbu
            usd_per, _ccy = price_map.get(sku, (None, "USD"))
            line_usd = (dbu * usd_per) if usd_per is not None else None
            if line_usd is not None:
                total_usd += line_usd
            by_ep.append(
                {
                    "sku_name": sku,
                    "endpoint_name": ep,
                    "dbu": dbu,
                    "usd_per_dbu": usd_per,
                    "list_usd": line_usd,
                }
            )
        out["total_dbu"] = total_dbu
        out["total_list_usd"] = round(total_usd, 6)
        out["pricing_partial"] = any(x.get("usd_per_dbu") is None for x in by_ep)
        if out["pricing_partial"]:
            out["note"] += " Partial: missing list price for some SKUs."
        out["by_endpoint"] = by_ep
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def comparison_group_detail(group_id: str) -> dict[str, Any]:
    """Rows in AGENTOPS_INFERENCE_TABLE sharing comparison_group_id + token/cost compare."""
    gid, gerr = _sanitize_comparison_group_id(group_id)
    if gerr or not gid:
        return {"error": gerr or "invalid group_id", "group_id": group_id, "rows": []}

    ctx, err = _inference_table_ctx()
    if err or not ctx:
        return {"error": err or "no_context", "group_id": gid, "rows": []}

    s = get_settings()
    ccol = _comparison_sql_column(ctx["cols"], s.inference_comparison_group_column)
    if not ccol:
        return {
            "error": f"column '{s.inference_comparison_group_column}' not found in inference table",
            "group_id": gid,
            "rows": [],
        }

    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    g_esc = gid.replace("'", "''")
    sql = (
        f"SELECT * FROM {tbl} WHERE CAST(`{ccol}` AS STRING) = '{g_esc}' "
        f"ORDER BY `{tc}` ASC NULLS LAST LIMIT 64"
    )
    rq_col = _comparison_sql_column(ctx["cols"], "request_id")
    if not rq_col:
        return {"error": "request_id column not found", "group_id": gid, "rows": []}

    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
            colnames = [c[0] for c in cur.description] if cur.description else []
    except Exception as e:  # noqa: BLE001
        return {"error": str(e).strip()[:500], "group_id": gid, "rows": []}

    records: list[dict[str, Any]] = []
    request_ids: list[str] = []
    times_raw: list[Any] = []
    for row in rows:
        rec = {colnames[i]: row[i] for i in range(len(colnames))}
        rec_j = {str(k): _json_safe_value(v) for k, v in rec.items()}
        records.append(rec_j)
        rid = rec_j.get(rq_col)
        if rid:
            request_ids.append(str(rid))
        tv = rec.get(tc)
        if tv is not None:
            times_raw.append(tv)
    tmin = min(times_raw) if times_raw else None
    tmax = max(times_raw) if times_raw else None

    gw_map, gw_err = _fetch_ai_gateway_usage_batch(request_ids, limit=64)
    billing = billing_model_serving_between(tmin, tmax) if tmin is not None and tmax is not None else {}

    sum_tok = 0
    for rid in request_ids:
        u = gw_map.get(rid)
        if u and u.get("total_tokens") is not None:
            try:
                sum_tok += int(u["total_tokens"])
            except (TypeError, ValueError):
                pass

    total_pool_usd = billing.get("total_list_usd")
    if isinstance(total_pool_usd, (int, float)) and total_pool_usd < 0:
        total_pool_usd = None

    cmap_lower = {c.lower(): c for c in ctx["cols"]}
    dest_key = cmap_lower.get("destination_id")
    st_actual = cmap_lower.get("status_code")
    lat_actual = cmap_lower.get("latency_ms")

    out_rows: list[dict[str, Any]] = []
    for rec_j in records:
        rid = str(rec_j.get(rq_col) or "")
        gw = gw_map.get(rid) if rid else None
        tt = None
        if gw and gw.get("total_tokens") is not None:
            try:
                tt = int(gw["total_tokens"])
            except (TypeError, ValueError):
                tt = None
        est_usd = None
        if total_pool_usd is not None and sum_tok > 0 and tt is not None:
            est_usd = round(float(total_pool_usd) * (tt / float(sum_tok)), 6)

        ev = rec_j.get(tc)
        ev_s = ev if isinstance(ev, str) else (ev.isoformat() if hasattr(ev, "isoformat") else str(ev) if ev else None)

        dest_val = rec_j.get(dest_key) if dest_key else None
        inf_st = rec_j.get(st_actual) if st_actual else None
        inf_lat = rec_j.get(lat_actual) if lat_actual else None
        out_rows.append(
            {
                "request_id": rid or None,
                "event_time": ev_s,
                "destination_id": str(dest_val) if dest_val is not None else None,
                "model_or_destination": (gw.get("destination_model") if gw else None)
                or (gw.get("destination_name") if gw else None)
                or (str(dest_val) if dest_val is not None else None),
                "status_code": (gw.get("status_code") if gw else inf_st),
                "latency_ms": (gw.get("latency_ms") if gw else inf_lat),
                "input_tokens": gw.get("input_tokens") if gw else None,
                "output_tokens": gw.get("output_tokens") if gw else None,
                "total_tokens": tt,
                "est_list_usd_prorated": est_usd,
                "ai_gateway_usage": gw,
            }
        )

    return {
        "group_id": gid,
        "rows": out_rows,
        "comparison_column": ccol,
        "tokens_sum_gateway": sum_tok if sum_tok else None,
        "billing_window": {
            "total_list_usd": billing.get("total_list_usd"),
            "total_dbu": billing.get("total_dbu"),
            "error": billing.get("error"),
            "note": billing.get("note"),
            "window_start": billing.get("window_start"),
            "window_end": billing.get("window_end"),
        },
        "ai_gateway_batch_error": gw_err,
        "note": (
            "est_list_usd_prorated splits billing MODEL_SERVING list USD for the group's time window "
            "by each run's AI Gateway total_tokens. If a row has no gateway usage, it gets $0."
        ),
    }


def _collect_billing_endpoint_match_terms(*labels: str) -> list[str]:
    """Tokens for fuzzy-matching Databricks billing.usage endpoint_name (e.g. databricks-qwen35-122b-a10b)."""
    seen: set[str] = set()
    out: list[str] = []
    stop = {
        "the",
        "and",
        "for",
        "api",
        "v1",
        "chat",
        "model",
        "text",
        "instruct",
        "completion",
        "openai",
        "mlflow",
    }

    def add(tok: str) -> None:
        t = tok.strip().lower()
        if len(t) < 2 or t in stop:
            return
        if t not in seen:
            seen.add(t)
            out.append(t)

    for label in labels:
        if not label or not str(label).strip():
            continue
        raw = str(label).strip()
        s = re.sub(r"[^a-zA-Z0-9]+", " ", raw.lower()).strip()
        for tok in s.split():
            add(tok)
        compact = re.sub(r"[^a-z0-9]+", "", raw.lower())
        if 4 <= len(compact) <= 48:
            add(compact)
    return out[:24]


def _sql_billing_endpoint_like_clause(terms: list[str]) -> str:
    """Extra WHERE fragment on usage_metadata.endpoint_name."""
    if not terms:
        return " AND 1=0 "
    ep_expr = (
        "LOWER(COALESCE(NULLIF(TRIM(CAST(usage_metadata.endpoint_name AS STRING)), ''), '(none)'))"
    )
    parts: list[str] = []
    for t in terms[:20]:
        safe = "".join(c for c in t.lower() if c.isalnum() or c in "-_")[:48]
        if len(safe) < 2:
            continue
        esc = safe.replace("'", "''")
        parts.append(f"{ep_expr} LIKE LOWER(CONCAT('%', '{esc}', '%'))")
    if not parts:
        return " AND 1=0 "
    return " AND (" + " OR ".join(parts) + ") "


def _resolve_billing_terms_for_pinned_request(ctx: dict[str, Any] | None, rid: str) -> list[str]:
    """Gateway usage row first; else parse model name from inference response JSON for this request_id."""
    gw_row, _ = _fetch_ai_gateway_usage_row(rid)
    if gw_row:
        labels: list[str] = []
        for k in ("destination_model", "destination_name"):
            v = gw_row.get(k)
            if v and str(v).strip():
                labels.append(str(v).strip())
        if labels:
            return _collect_billing_endpoint_match_terms(*labels)
    if not ctx:
        return []
    cmap = {c.lower(): c for c in ctx["cols"]}
    if "request_id" not in cmap or "response" not in cmap:
        return []
    tbl = ctx["table_sql"]
    rq = cmap["request_id"]
    rsp = cmap["response"]
    esc = rid.replace("'", "''")
    sql = f"SELECT CAST(`{rsp}` AS STRING) AS r FROM {tbl} WHERE CAST(`{rq}` AS STRING) = '{esc}' LIMIT 1"
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
        if not row or row[0] is None:
            return []
        txt = str(row[0])
        if not txt.strip():
            return []
        try:
            parsed = json.loads(txt)
        except json.JSONDecodeError:
            return []
        mname = _response_model_name(parsed)
        if mname:
            return _collect_billing_endpoint_match_terms(mname)
    except Exception:  # noqa: BLE001
        return []
    return []


def _usd_per_dbu_from_pricing_json(pricing_raw: Any) -> float | None:
    if pricing_raw is None:
        return None
    try:
        if isinstance(pricing_raw, str):
            data = json.loads(pricing_raw)
        elif isinstance(pricing_raw, dict):
            data = pricing_raw
        else:
            return None
        eff = data.get("effective_list")
        if isinstance(eff, dict) and eff.get("default") is not None:
            return float(eff["default"])
        if data.get("default") is not None:
            return float(data["default"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return None


def billing_model_serving_cost(
    hours: int,
    *,
    endpoint_match_terms: list[str] | None = None,
) -> dict[str, Any]:
    """List-price USD from MODEL_SERVING TOKEN rows (DBU) * list_prices.

    endpoint_match_terms:
      None  — workspace-wide (all MODEL_SERVING endpoints in window).
      []    — caller scoped cost but no endpoint tokens derived → return zeros (no false workspace rollup).
      [...] — OR of LIKE filters on usage_metadata.endpoint_name.
    """
    ws = get_settings().workspace_id.strip()
    h = max(1, min(24 * 90, int(hours)))
    scoped = endpoint_match_terms is not None
    out: dict[str, Any] = {
        "hours": h,
        "total_dbu": None,
        "total_list_usd": None,
        "currency_code": "USD",
        "by_endpoint": [],
        "pricing_partial": False,
        "error": None,
        "attribution": "endpoint_unmatched" if scoped and not endpoint_match_terms else (
            "endpoint_filtered" if scoped else "workspace"
        ),
        "endpoint_match_terms": list(endpoint_match_terms) if scoped else None,
        "note": (
            "Estimate: system.billing.usage (usage_type=TOKEN, MODEL_SERVING) times "
            "effective_list price in system.billing.list_prices. Not an invoice; excludes discounts."
        ),
    }
    if not ws.isdigit():
        out["error"] = "set DATABRICKS_WORKSPACE_ID (digits) for billing.usage"
        return out
    wpred = f"workspace_id = {int(ws)}"
    ep_extra = _sql_billing_endpoint_like_clause(endpoint_match_terms) if scoped else ""
    sql_agg = (
        "SELECT sku_name, "
        "COALESCE(NULLIF(TRIM(CAST(usage_metadata.endpoint_name AS STRING)), ''), "
        "'(none)') AS endpoint_name, "
        "CAST(SUM(usage_quantity) AS DOUBLE) AS dbu "
        "FROM system.billing.usage "
        f"WHERE {wpred} "
        f"AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS "
        "AND usage_type = 'TOKEN' "
        "AND billing_origin_product = 'MODEL_SERVING' "
        f"{ep_extra}"
        "GROUP BY 1, 2 "
        "ORDER BY dbu DESC NULLS LAST"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_agg)
            agg_rows = cur.fetchall() or []
            if not agg_rows and not scoped:
                sql_broad = (
                    "SELECT sku_name, "
                    "COALESCE(NULLIF(TRIM(CAST(usage_metadata.endpoint_name AS STRING)), ''), "
                    "'(none)') AS endpoint_name, "
                    "CAST(SUM(usage_quantity) AS DOUBLE) AS dbu "
                    "FROM system.billing.usage "
                    f"WHERE {wpred} "
                    f"AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS "
                    "AND usage_type = 'TOKEN' "
                    "GROUP BY 1, 2 "
                    "ORDER BY dbu DESC NULLS LAST"
                )
                cur.execute(sql_broad)
                agg_rows = cur.fetchall() or []
                if agg_rows:
                    out["note"] += (
                        " Broader rollup: all TOKEN rows (MODEL_SERVING-only filter returned none)."
                    )
        if not agg_rows:
            out["total_dbu"] = 0.0
            out["total_list_usd"] = 0.0
            out["by_endpoint"] = []
            if scoped:
                if endpoint_match_terms:
                    out["note"] += (
                        " Scoped list-price filter matched no billing rows in this window "
                        "(endpoint names may differ from gateway labels, or billing lags traffic)."
                    )
                else:
                    out["note"] += (
                        " Cost scope active but no tokens derived to match billing endpoint_name "
                        "(need AI Gateway usage or a parseable response.model in logs)."
                    )
            else:
                out["note"] += (
                    " If zero while AI Gateway shows traffic: billing.usage can lag 24–48h, rows may use another "
                    "billing_origin_product, or DATABRICKS_WORKSPACE_ID may not match system.billing rows."
                )
            return out

        skus: list[str] = []
        for r in agg_rows:
            if r and r[0] is not None:
                skus.append(str(r[0]))
        uniq = sorted(set(skus))
        escaped = ",".join("'" + s.replace("'", "''") + "'" for s in uniq)
        sql_prices = f"SELECT sku_name, currency_code, pricing FROM system.billing.list_prices WHERE sku_name IN ({escaped})"
        price_map: dict[str, tuple[float | None, str]] = {}
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_prices)
            for prow in cur.fetchall() or []:
                if not prow:
                    continue
                sku = str(prow[0])
                ccy = str(prow[1] or "USD")
                usd = _usd_per_dbu_from_pricing_json(prow[2])
                # latest row wins if duplicate sku (same price usually)
                price_map[sku] = (usd, ccy)

        by_ep: list[dict[str, Any]] = []
        total_dbu = 0.0
        total_usd = 0.0
        for r in agg_rows:
            sku = str(r[0]) if r[0] else "unknown"
            ep = str(r[1]) if r[1] else "unknown"
            dbu = float(r[2] or 0.0)
            total_dbu += dbu
            usd_per, _ccy = price_map.get(sku, (None, "USD"))
            line_usd = (dbu * usd_per) if usd_per is not None else None
            if line_usd is not None:
                total_usd += line_usd
            by_ep.append(
                {
                    "sku_name": sku,
                    "endpoint_name": ep,
                    "dbu": dbu,
                    "usd_per_dbu": usd_per,
                    "list_usd": line_usd,
                }
            )
        out["total_dbu"] = total_dbu
        out["total_list_usd"] = round(total_usd, 6)
        out["pricing_partial"] = any(x.get("usd_per_dbu") is None for x in by_ep)
        if out["pricing_partial"]:
            out["note"] += " Partial: missing list price for some SKUs."
        if scoped and endpoint_match_terms:
            out["attribution"] = "endpoint_filtered"
            out["note"] += (
                " List price rows below are filtered by gateway/log model labels vs billing endpoint_name "
                "(fuzzy match; verify in system.billing.usage)."
            )
        out["by_endpoint"] = by_ep
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def _gateway_model_match_sql(gateway_model: str) -> str:
    """Boolean SQL for one gateway model (no leading AND). Wildcards in input are stripped."""
    gm = "".join(c for c in str(gateway_model).strip() if c not in "%_\\")
    if not gm:
        return ""
    esc = gm.replace("'", "''")
    return (
        f"(LOWER(TRIM(COALESCE(CAST(destination_model AS STRING), CAST(destination_name AS STRING), ''))) = LOWER(TRIM('{esc}')) "
        f"OR LOWER(TRIM(CAST(destination_model AS STRING))) LIKE LOWER(CONCAT('%', '{esc}', '%')) "
        f"OR LOWER(TRIM(CAST(destination_name AS STRING))) LIKE LOWER(CONCAT('%', '{esc}', '%')) "
        f"OR LOWER(TRIM(CAST(destination_id AS STRING))) LIKE LOWER(CONCAT('%', '{esc}', '%')))"
    )


def _gateway_model_sql_fragment(gateway_model: str) -> str:
    inner = _gateway_model_match_sql(gateway_model)
    return f" AND ({inner}) " if inner else ""


def _gateway_models_sql_fragment(gateway_models: list[str] | None) -> str:
    if not gateway_models:
        return ""
    parts = [_gateway_model_match_sql(m) for m in gateway_models]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return " AND (" + " OR ".join(f"({p})" for p in parts) + ") "


def ai_gateway_usage_rollup(
    hours: int,
    gateway_model: str | None = None,
    gateway_models: list[str] | None = None,
    *,
    request_id: str | None = None,
    gateway_no_rows: bool = False,
) -> dict[str, Any]:
    """Aggregate token usage by model from AI Gateway system table."""
    h = max(1, min(24 * 90, int(hours)))
    w = _ai_gateway_workspace_sql()
    gm_models_eff: list[str] | None = None
    if gateway_models:
        gm_models_eff = [str(m).strip() for m in gateway_models if m and str(m).strip()]
        if not gm_models_eff:
            gm_models_eff = None
    gm_clause = ""
    if gm_models_eff:
        gm_clause = _gateway_models_sql_fragment(gm_models_eff)
    elif gateway_model and str(gateway_model).strip():
        gm_clause = _gateway_model_sql_fragment(str(gateway_model).strip())
    rid_clause = ""
    rid = (request_id or "").strip()
    if rid:
        esc = rid.replace("'", "''")
        rid_clause = f" AND request_id = '{esc}' "
    no_row_clause = " AND 1=0 " if gateway_no_rows else ""
    out: dict[str, Any] = {
        "hours": h,
        "total_requests": None,
        "total_input_tokens": None,
        "total_output_tokens": None,
        "total_tokens": None,
        "by_model": [],
        "error": None,
        "note": "",
        "gateway_model_filter": gateway_model.strip() if gateway_model and str(gateway_model).strip() else None,
        "gateway_models_filter": gm_models_eff,
        "gateway_request_id_filter": rid or None,
        "gateway_no_rows": bool(gateway_no_rows),
    }
    sql_tot = (
        "SELECT COUNT(*), "
        "COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(total_tokens), 0) "
        "FROM system.ai_gateway.usage "
        f"WHERE event_time >= current_timestamp() - INTERVAL {h} HOURS{w}{gm_clause}{rid_clause}{no_row_clause}"
    )
    sql_by = (
        "SELECT "
        "COALESCE(NULLIF(TRIM(CAST(destination_model AS STRING)), ''), "
        "NULLIF(TRIM(CAST(destination_name AS STRING)), ''), "
        "CAST(destination_id AS STRING), 'unknown') AS m, "
        "COUNT(*) AS c, "
        "COALESCE(SUM(input_tokens), 0) AS tin, "
        "COALESCE(SUM(output_tokens), 0) AS tout, "
        "COALESCE(SUM(total_tokens), 0) AS ttot "
        "FROM system.ai_gateway.usage "
        f"WHERE event_time >= current_timestamp() - INTERVAL {h} HOURS{w}{gm_clause}{rid_clause}{no_row_clause}"
        "GROUP BY 1 ORDER BY ttot DESC NULLS LAST LIMIT 25"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_tot)
            tr = cur.fetchone()
            if tr:
                out["total_requests"] = int(tr[0] or 0)
                out["total_input_tokens"] = int(tr[1] or 0)
                out["total_output_tokens"] = int(tr[2] or 0)
                out["total_tokens"] = int(tr[3] or 0)

            cur.execute(sql_by)
            rows = cur.fetchall() or []
        out["by_model"] = [
            {
                "model": str(r[0]) if r[0] is not None else "unknown",
                "requests": int(r[1] or 0),
                "input_tokens": int(r[2] or 0),
                "output_tokens": int(r[3] or 0),
                "total_tokens": int(r[4] or 0),
            }
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def _inference_request_id_predicate(cmap: dict[str, str], request_id: str | None) -> str:
    rid = (request_id or "").strip()
    if not rid or "request_id" not in cmap:
        return ""
    esc = rid.replace("'", "''")
    return f" AND CAST(`{cmap['request_id']}` AS STRING) = '{esc}' "


def _distinct_destination_ids_from_inference(
    ctx: dict[str, Any],
    source_predicate_sql: str,
    hours: int,
    *,
    max_ids: int = 48,
) -> list[str]:
    """destination_id values appearing in inference logs for the scoped source predicate (same window)."""
    cmap = {c.lower(): c for c in ctx["cols"]}
    if "destination_id" not in cmap:
        return []
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    did = cmap["destination_id"]
    lim = max(1, min(int(max_ids), 80))
    hi = max(1, min(24 * 90, int(hours)))
    sql = (
        f"SELECT DISTINCT TRIM(CAST(`{did}` AS STRING)) AS d FROM {tbl} "
        f"WHERE `{tc}` >= current_timestamp() - INTERVAL {hi} HOURS "
        f"{source_predicate_sql} "
        f"AND `{did}` IS NOT NULL AND TRIM(CAST(`{did}` AS STRING)) != '' "
        f"LIMIT {lim}"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
        out: list[str] = []
        for r in rows:
            if r and r[0] is not None:
                s = str(r[0]).strip()
                if s and s not in out:
                    out.append(s)
        return out
    except Exception:  # noqa: BLE001
        return []


def _response_model_name(parsed: Any) -> str | None:
    if not isinstance(parsed, dict):
        return None
    top = parsed.get("model")
    if isinstance(top, str) and top.strip():
        return top.strip()
    choices = parsed.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0] if isinstance(choices[0], dict) else {}
        if isinstance(ch0, dict):
            inner = ch0.get("model")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return None


def _assistant_text_preview(parsed: Any, max_len: int = 320) -> str | None:
    if not isinstance(parsed, dict):
        return None
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    msg = (choices[0] or {}).get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        s = content.strip()
        return s if len(s) <= max_len else s[: max_len - 1] + "…"
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        if parts:
            s = " ".join(parts).strip()
            return s if len(s) <= max_len else s[: max_len - 1] + "…"
    return None


def _build_request_lineage_graph(
    record: dict[str, Any],
    parsed_resp: Any,
    gateway: dict[str, Any] | None,
    reasoning: str | None,
) -> dict[str, Any]:
    """Nodes + edges for a left-to-right request journey (not UC table lineage)."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    def push(title: str, detail: str, kind: str) -> str:
        nid = f"n{len(nodes)}"
        nodes.append(
            {
                "id": nid,
                "title": title,
                "detail": (detail or "")[:900],
                "kind": kind,
            }
        )
        return nid

    requester = str(record.get("requester") or "")
    if not requester and gateway:
        requester = str(gateway.get("requester") or "")
    push("Caller", requester or "Authenticated client", "caller")

    url = str(record.get("url") or "")
    if not url and gateway:
        url = str(gateway.get("url") or "")
    api = str(record.get("api_type") or "")
    if not api and gateway:
        api = str(gateway.get("api_type") or "")
    g_lines = [x for x in (api, url) if x]
    push("AI Gateway", "\n".join(g_lines) if g_lines else "AI Gateway route", "gateway")

    dest = str(record.get("destination_id") or "")
    if not dest and gateway and gateway.get("destination_id") is not None:
        dest = str(gateway.get("destination_id"))
    model = _response_model_name(parsed_resp) or ""
    if not model and gateway:
        model = str(gateway.get("destination_model") or gateway.get("destination_name") or "")
    route_bits = [f"destination: {dest}"] if dest else []
    if model:
        route_bits.append(f"model: {model}")
    push("Routed model", "\n".join(route_bits) if route_bits else "Routing metadata in payload", "route")

    lat_raw = record.get("latency_ms")
    if gateway and gateway.get("latency_ms") is not None:
        lat_raw = gateway.get("latency_ms")
    try:
        lat_f = float(lat_raw) if lat_raw is not None else None
    except (TypeError, ValueError):
        lat_f = None
    lat_s = f"{lat_f:.0f} ms" if lat_f is not None else "—"
    tin = tout = ttot = None
    if gateway:
        tin, tout, ttot = gateway.get("input_tokens"), gateway.get("output_tokens"), gateway.get("total_tokens")
    tok_block = "Token counts: open Cost tab for rollups."
    if ttot is not None:
        try:
            tin_i, tout_i, tot_i = int(float(tin or 0)), int(float(tout or 0)), int(float(ttot))
        except (TypeError, ValueError):
            tin_i, tout_i, tot_i = 0, 0, 0
        tok_block = f"input {tin_i:,} · output {tout_i:,} · total {tot_i:,} (AI Gateway metering)"
    run_detail = f"Latency: {lat_s}\n{tok_block}"
    if reasoning:
        rshort = reasoning if len(reasoning) <= 240 else reasoning[:239] + "…"
        run_detail += f"\nReasoning (excerpt): {rshort}"
    push("Model run", run_detail, "compute")

    st_raw = record.get("status_code")
    if gateway and gateway.get("status_code") is not None:
        st_raw = gateway.get("status_code")
    try:
        st_i = int(st_raw) if st_raw is not None else None
    except (TypeError, ValueError):
        st_i = None
    ans = _assistant_text_preview(parsed_resp)
    if not ans and reasoning:
        ans = "(Structured / reasoning output — see JSON blocks below.)"
    resp_detail = f"HTTP {st_i if st_i is not None else '—'}"
    if ans:
        resp_detail += f"\n{ans}"
    push("Response", resp_detail, "response")

    for i in range(len(nodes) - 1):
        edges.append({"from": nodes[i]["id"], "to": nodes[i + 1]["id"]})
    return {"nodes": nodes, "edges": edges}


def _inference_table_ctx() -> _CTX_OK | _CTX_ERR:
    s = get_settings()
    cache_key = "|".join(
        [
            (s.inference_table_fqn or "").strip(),
            (s.inference_tables_fqn or "").strip(),
            (s.inference_schema_fqn or "").strip(),
            (s.inference_table_name_suffix or "").strip(),
            (s.inference_time_column or "").strip(),
        ]
    )
    now = time.monotonic()
    hit = _inference_ctx_cache.get(cache_key)
    if hit and (now - hit[0]) < _INFERENCE_CTX_TTL_SEC:
        _ts, ctx_hit, err_hit = hit
        if err_hit is not None:
            return None, err_hit
        if ctx_hit is not None:
            return ctx_hit, None
    fqns, _fqns_note = inference_fqn_list()
    if not fqns:
        _inference_ctx_cache[cache_key] = (time.monotonic(), None, "inference_table_not_configured")
        return None, "inference_table_not_configured"
    try:
        table_sql = inference_query_table_sql(fqns)
        describe_sql = quote_fqn(fqns[0])
    except ValueError as e:
        msg = str(e)
        _inference_ctx_cache[cache_key] = (time.monotonic(), None, msg)
        return None, msg
    cols, derr = describe_columns(describe_sql)
    if derr:
        _inference_ctx_cache[cache_key] = (time.monotonic(), None, derr)
        return None, derr
    if not cols:
        _inference_ctx_cache[cache_key] = (time.monotonic(), None, "no_columns")
        return None, "no_columns"
    time_col = resolve_time_column(cols, s.inference_time_column)
    if not time_col:
        _inference_ctx_cache[cache_key] = (time.monotonic(), None, "no_time_column")
        return None, "no_time_column"
    if len(fqns) > 1:
        cols = list(cols) + ["_agentops_source_table"]
    avail = set(cols)
    fqn_label = fqns[0] if len(fqns) == 1 else f"{len(fqns)} tables ({', '.join(x.split('.')[-1] for x in fqns)})"
    ctx = {
        "fqn": fqns[0],
        "fqns": fqns,
        "fqn_label": fqn_label,
        "table_sql": table_sql,
        "time_col": time_col,
        "cols": cols,
        "status_col": pick_col(avail, STATUS_CANDIDATES),
        "latency_col": pick_col(avail, LATENCY_CANDIDATES),
        "dest_col": pick_col(
            avail,
            ("destination_id", "endpoint_name", "model_name", "gateway_endpoint"),
        ),
    }
    _inference_ctx_cache[cache_key] = (time.monotonic(), ctx, None)
    return ctx, None


def build_runtime_flow(ctx: dict[str, Any], days: int = 7) -> dict[str, Any]:
    """Observed AI Gateway / serving paths from logged traffic (works without UC system lineage)."""
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    cmap = {c.lower(): c for c in ctx["cols"]}
    window = max(1, min(90, int(days)))
    out: dict[str, Any] = {
        "window_days": int(days),
        "distinct_callers": None,
        "total_requests": None,
        "routes": [],
        "models": [],
        "error": None,
    }
    try:
        has_req = "requester" in cmap
        has_resp = "response" in cmap
        if has_req:
            sql0 = (
                f"SELECT COUNT(DISTINCT `{cmap['requester']}`), COUNT(*) FROM {tbl} "
                f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS"
            )
        else:
            sql0 = (
                f"SELECT CAST(NULL AS BIGINT), COUNT(*) FROM {tbl} "
                f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS"
            )
        dest_e = f"`{cmap['destination_id']}`" if "destination_id" in cmap else "CAST(NULL AS STRING)"
        url_e = f"`{cmap['url']}`" if "url" in cmap else "CAST(NULL AS STRING)"
        api_e = f"`{cmap['api_type']}`" if "api_type" in cmap else "CAST(NULL AS STRING)"
        sql_r = (
            f"SELECT CAST({dest_e} AS STRING), CAST({url_e} AS STRING), CAST({api_e} AS STRING), "
            f"COUNT(*) AS c FROM {tbl} "
            f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS "
            f"GROUP BY 1, 2, 3 ORDER BY c DESC NULLS LAST LIMIT 30"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql0)
            r0 = cur.fetchone()
            if r0:
                if r0[0] is not None:
                    out["distinct_callers"] = int(r0[0])
                out["total_requests"] = int(r0[1] or 0)
            cur.execute(sql_r)
            for row in cur.fetchall() or []:
                out["routes"].append(
                    {
                        "destination_id": row[0],
                        "url": row[1],
                        "api_type": row[2],
                        "requests": int(row[3] or 0),
                    }
                )
            if has_resp:
                sql_m = (
                    f"SELECT get_json_object(CAST(`{cmap['response']}` AS STRING), '$.model') AS m, "
                    f"COUNT(*) AS c FROM {tbl} "
                    f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS "
                    f"AND `{cmap['response']}` IS NOT NULL "
                    f"GROUP BY 1 ORDER BY c DESC NULLS LAST LIMIT 15"
                )
                cur.execute(sql_m)
                for mrow in cur.fetchall() or []:
                    out["models"].append({"model": mrow[0], "requests": int(mrow[1] or 0)})
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    if (out.get("total_requests") or 0) == 0 and not out.get("error"):
        hours_gw = max(1, min(90 * 24, window * 24))
        gw = ai_gateway_usage_rollup(hours_gw)
        if not gw.get("error") and int(gw.get("total_requests") or 0) > 0:
            out["total_requests"] = int(gw["total_requests"] or 0)
            if not out["models"]:
                out["models"] = [
                    {"model": r.get("model"), "requests": int(r.get("requests") or 0)}
                    for r in (gw.get("by_model") or [])
                ]
            if not out["routes"]:
                out["routes"] = [
                    {
                        "destination_id": r.get("model"),
                        "url": None,
                        "api_type": "ai_gateway.usage",
                        "requests": int(r.get("requests") or 0),
                    }
                    for r in (gw.get("by_model") or [])
                ]
            out["note"] = (
                "Filled from system.ai_gateway.usage — inference log had no rows in this window."
            )
    return out


def health_timeseries(
    hours: int = 168,
    source_table: str | None = None,
    *,
    source_tables: list[str] | None = None,
) -> dict[str, Any]:
    """Hourly request + error counts."""
    ctx, err = _inference_table_ctx()
    out: dict[str, Any] = {
        "hours": int(hours),
        "buckets": [],
        "error": err,
    }
    if err or not ctx:
        return out
    tc = ctx["time_col"]
    sc = ctx["status_col"]
    tbl = ctx["table_sql"]
    if source_tables:
        extra = _ctx_source_predicate_multi(ctx, source_tables)
    else:
        extra = _ctx_source_predicate(ctx, source_table)
    err_case = "0"
    if sc:
        err_case = (
            f"CASE WHEN CAST(`{sc}` AS DOUBLE) >= 400 "
            f"OR CAST(`{sc}` AS DOUBLE) < 100 THEN 1 ELSE 0 END"
        )
    sql = (
        f"SELECT date_trunc('HOUR', `{tc}`) AS bucket, "
        f"COUNT(*) AS requests, "
        f"SUM({err_case}) AS errors "
        f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {int(hours)} HOURS "
        f"{extra} GROUP BY 1 ORDER BY 1 ASC"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
        buckets: list[dict[str, Any]] = []
        for r in rows or []:
            if not r or r[0] is None:
                continue
            b = r[0]
            if hasattr(b, "isoformat"):
                bt = b.isoformat()
            else:
                bt = str(b)
            buckets.append(
                {
                    "bucket": bt,
                    "requests": int(r[1] or 0),
                    "errors": int(r[2] or 0),
                }
            )
        out["buckets"] = buckets
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def health_slo_summary(
    p95_target_ms: float = 2000.0,
    error_budget_pct: float = 1.0,
    source_table: str | None = None,
    *,
    source_tables: list[str] | None = None,
) -> dict[str, Any]:
    """Global 24h SLO + per-segment rollups vs targets."""
    ctx, err = _inference_table_ctx()
    out: dict[str, Any] = {
        "p95_target_ms": p95_target_ms,
        "error_budget_pct": error_budget_pct,
        "global_p95_ms": None,
        "global_error_rate_pct": None,
        "agents_breaching_p95": 0,
        "agents_over_error_budget": 0,
        "rollups": [],
        "error": err,
    }
    if err or not ctx:
        return out
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    lc = ctx["latency_col"]
    sc = ctx["status_col"]
    if source_tables:
        extra = _ctx_source_predicate_multi(ctx, source_tables)
    else:
        extra = _ctx_source_predicate(ctx, source_table)
    if lc and sc:
        gsql = (
            f"SELECT approx_percentile(`{lc}`, 0.95) AS p95, "
            f"AVG(CASE WHEN CAST(`{sc}` AS DOUBLE) >= 400 "
            f"OR CAST(`{sc}` AS DOUBLE) < 100 THEN 1.0 ELSE 0.0 END) AS er "
            f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL 24 HOURS {extra}"
        )
    elif lc:
        gsql = (
            f"SELECT approx_percentile(`{lc}`, 0.95) AS p95, CAST(0.0 AS DOUBLE) AS er "
            f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL 24 HOURS {extra}"
        )
    else:
        gsql = None
    try:
        if gsql:
            with sql_connection() as conn:
                cur = conn.cursor()
                cur.execute(gsql)
                row = cur.fetchone()
            if row:
                out["global_p95_ms"] = float(row[0]) if row[0] is not None else None
                out["global_error_rate_pct"] = (
                    float(row[1]) * 100.0 if row[1] is not None else None
                )
        rollups = inference_agent_rollups(limit=50)
        out["rollups"] = rollups
        bp = sum(1 for r in rollups if float(r.get("p95_latency_ms") or 0) > p95_target_ms)
        be = sum(1 for r in rollups if float(r.get("error_rate_pct") or 0) > error_budget_pct)
        out["agents_breaching_p95"] = bp
        out["agents_over_error_budget"] = be
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def cost_summary(
    hours: int = 168,
    source_table: str | None = None,
    gateway_model: str | None = None,
    *,
    source_tables: list[str] | None = None,
    gateway_models: list[str] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Payload token proxy; AI Gateway tokens; billing list-price USD for model serving."""
    st_eff: list[str] | None = None
    if source_tables:
        st_eff = [str(s).strip() for s in source_tables if s and str(s).strip()]
        if not st_eff:
            st_eff = None
    gw_eff: list[str] | None = None
    if gateway_models:
        gw_eff = [str(g).strip() for g in gateway_models if g and str(g).strip()]
        if not gw_eff:
            gw_eff = None

    rid = (request_id or "").strip() or None
    ctx, err = _inference_table_ctx()
    derived_gateway_destination_ids: list[str] | None = None

    if rid:
        ag = ai_gateway_usage_rollup(hours, request_id=rid)
    elif gw_eff:
        ag = ai_gateway_usage_rollup(hours, gateway_models=gw_eff)
    elif gateway_model and str(gateway_model).strip():
        ag = ai_gateway_usage_rollup(hours, gateway_model=str(gateway_model).strip())
    elif not err and ctx:
        has_inference_scope = bool(st_eff) or bool(source_table and str(source_table).strip())
        if has_inference_scope:
            if st_eff:
                pred_base = _ctx_source_predicate_multi(ctx, st_eff)
            else:
                pred_base = _ctx_source_predicate(ctx, source_table)
            derived_gateway_destination_ids = _distinct_destination_ids_from_inference(ctx, pred_base, hours)
            if derived_gateway_destination_ids:
                ag = ai_gateway_usage_rollup(hours, gateway_models=derived_gateway_destination_ids)
            else:
                ag = ai_gateway_usage_rollup(hours, gateway_no_rows=True)
        else:
            ag = ai_gateway_usage_rollup(hours)
    else:
        ag = ai_gateway_usage_rollup(hours)

    has_focus = bool(rid or st_eff or gw_eff or (source_table and str(source_table).strip()))
    billing_terms_param: list[str] | None = None
    if has_focus:
        if rid:
            billing_terms_param = _resolve_billing_terms_for_pinned_request(ctx if (ctx and not err) else None, rid)
        else:
            bm = (ag or {}).get("by_model") or []
            labs = [str(x.get("model") or "") for x in bm if x.get("model")]
            if labs:
                billing_terms_param = _collect_billing_endpoint_match_terms(*labs)
            else:
                billing_terms_param = []

    bill = billing_model_serving_cost(
        hours,
        endpoint_match_terms=billing_terms_param if has_focus else None,
    )
    out: dict[str, Any] = {
        "hours": int(hours),
        "method": "char_length_over_4_proxy",
        "note": "",
        "total_est_tokens": None,
        "by_destination": [],
        "hourly": [],
        "error": err,
        "ai_gateway": ag,
        "billing": bill,
        "token_primary_source": "none",
        "filter": {
            "source_table": source_table,
            "gateway_model": gateway_model,
            "source_tables": st_eff,
            "gateway_models": gw_eff,
            "request_id": rid,
            "gateway_destination_ids_derived": derived_gateway_destination_ids,
            "billing_endpoint_terms": billing_terms_param if has_focus else None,
            "resolved_fqn": resolve_source_table_fqn(source_table) if source_table else None,
        },
    }
    if err or not ctx:
        if not ag.get("error"):
            out["token_primary_source"] = "ai_gateway"
        if derived_gateway_destination_ids is None and (st_eff or (source_table and str(source_table).strip())):
            out["note"] = (
                (out.get("note") or "")
                + " Scoped inference selected but default agent logs table is unavailable — "
                "AI Gateway totals are workspace-wide."
            ).strip()
        out["error"] = err
        return out

    cmap = {c.lower(): c for c in ctx["cols"]}
    cols_l = set(cmap.keys())
    tc = ctx["time_col"]
    tbl = ctx["table_sql"]
    if st_eff:
        pred_base = _ctx_source_predicate_multi(ctx, st_eff)
    else:
        pred_base = _ctx_source_predicate(ctx, source_table)
    pred = pred_base + _inference_request_id_predicate(cmap, rid)
    if "_agentops_source_table" in cols_l:
        group_dc = "`_agentops_source_table`"
    elif ctx.get("dest_col"):
        group_dc = f"`{ctx['dest_col']}`"
    else:
        group_dc = "CAST('(all)' AS STRING)"

    if "request" in cols_l and "response" in cols_l:
        rqc, rsc = cmap["request"], cmap["response"]
        tok = (
            f"(LENGTH(CAST(`{rqc}` AS STRING)) + LENGTH(CAST(`{rsc}` AS STRING))) / 4.0"
        )
    elif "total_tokens" in cols_l:
        tok = f"COALESCE(CAST(`{cmap['total_tokens']}` AS DOUBLE), 0.0)"
    elif "input_tokens" in cols_l and "output_tokens" in cols_l:
        tok = (
            f"(COALESCE(CAST(`{cmap['input_tokens']}` AS DOUBLE), 0.0) + "
            f"COALESCE(CAST(`{cmap['output_tokens']}` AS DOUBLE), 0.0))"
        )
    else:
        tok = "CAST(0.0 AS DOUBLE)"
    sql_total = (
        f"SELECT SUM({tok}) AS t "
        f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {int(hours)} HOURS {pred}"
    )
    sql_by = (
        f"SELECT {group_dc} AS dest, "
        f"COUNT(*) AS requests, "
        f"SUM({tok}) AS est_tokens "
        f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {int(hours)} HOURS {pred} "
        f"GROUP BY 1 ORDER BY est_tokens DESC NULLS LAST LIMIT 25"
    )
    sql_hour = (
        f"SELECT date_trunc('HOUR', `{tc}`) AS bucket, "
        f"SUM({tok}) AS est_tokens "
        f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {int(hours)} HOURS {pred} "
        f"GROUP BY 1 ORDER BY 1 ASC"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_total)
            tr = cur.fetchone()
            out["total_est_tokens"] = float(tr[0]) if tr and tr[0] is not None else 0.0

            cur.execute(sql_by)
            by_rows = cur.fetchall() or []
            out["by_destination"] = [
                {
                    "destination": str(r[0]) if r[0] is not None else "—",
                    "requests": int(r[1] or 0),
                    "est_tokens": float(r[2] or 0),
                }
                for r in by_rows
            ]

            cur.execute(sql_hour)
            hr = cur.fetchall() or []
            out["hourly"] = [
                {
                    "bucket": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                    "est_tokens": float(r[1] or 0),
                }
                for r in hr
                if r and r[0] is not None
            ]
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    if tok == "CAST(0.0 AS DOUBLE)" and not out.get("error"):
        out["note"] = (
            (out.get("note") or "")
            + " Inference proxy has no request/response or token columns — hourly/destination use counts only; "
            "prefer AI Gateway totals for tokens."
        ).strip()
    if has_focus:
        out["note"] = (
            (out.get("note") or "")
            + " Payload charts = inference proxy (char/4 or log token columns), not AI Gateway metering — totals can "
            "differ from Gateway tokens. List price uses billing.usage when scope is active."
        ).strip()
    if not ag.get("error") and (ag.get("total_tokens") or 0) > 0:
        out["token_primary_source"] = "ai_gateway"
    elif out.get("total_est_tokens"):
        out["token_primary_source"] = "payload_proxy"
    return out


def list_traces(
    limit: int = 40,
    source_table: str | None = None,
    gateway_model: str | None = None,
    *,
    source_tables: list[str] | None = None,
    gateway_models: list[str] | None = None,
) -> dict[str, Any]:
    ctx, err = _inference_table_ctx()
    out: dict[str, Any] = {"traces": [], "error": err}
    if err or not ctx:
        return out
    cmap = {c.lower(): c for c in ctx["cols"]}
    if "request_id" not in cmap:
        out["error"] = "request_id column not found in inference table"
        return out
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    lim = max(1, min(200, int(limit)))
    s = get_settings()

    st_eff: list[str] | None = None
    if source_tables:
        st_eff = [str(s).strip() for s in source_tables if s and str(s).strip()]
        if not st_eff:
            st_eff = None
    gw_eff: list[str] | None = None
    if gateway_models:
        gw_eff = [str(g).strip() for g in gateway_models if g and str(g).strip()]
        if not gw_eff:
            gw_eff = None

    if st_eff:
        pred_st = _ctx_source_predicate_multi(ctx, st_eff)
    else:
        pred_st = _ctx_source_predicate(ctx, source_table)
    gm_extra = ""
    did_col = cmap.get("destination_id")
    if gw_eff and did_col:
        parts_like: list[str] = []
        for gm in gw_eff:
            gm_raw = "".join(c for c in str(gm).strip() if c not in "%_\\")
            if not gm_raw:
                continue
            esc = gm_raw.replace("'", "''")
            parts_like.append(
                f"LOWER(TRIM(CAST(`{did_col}` AS STRING))) LIKE "
                f"LOWER(CONCAT('%', '{esc}', '%'))"
            )
        if parts_like:
            gm_extra = " AND (" + " OR ".join(parts_like) + ") "
    elif gateway_model and str(gateway_model).strip() and did_col:
        gm_raw = "".join(c for c in str(gateway_model).strip() if c not in "%_\\")
        if gm_raw:
            esc = gm_raw.replace("'", "''")
            gm_extra = (
                f" AND LOWER(TRIM(CAST(`{did_col}` AS STRING))) LIKE "
                f"LOWER(CONCAT('%', '{esc}', '%')) "
            )

    def col(name: str) -> str | None:
        return cmap.get(name.lower())

    parts: list[str] = [
        f"`{cmap['request_id']}` AS request_id",
        f"`{tc}` AS event_time",
    ]
    for opt, sqltype in (
        ("status_code", "INT"),
        ("latency_ms", "DOUBLE"),
        ("destination_id", "STRING"),
        ("url", "STRING"),
        ("api_type", "STRING"),
        ("requester", "STRING"),
    ):
        c = col(opt)
        if c:
            parts.append(f"`{c}` AS {opt}")
        else:
            parts.append(f"CAST(NULL AS {sqltype}) AS {opt}")
    rq = col("request")
    rs = col("response")
    if rq:
        parts.append(f"SUBSTRING(CAST(`{rq}` AS STRING), 1, 320) AS request_preview")
    else:
        parts.append("CAST(NULL AS STRING) AS request_preview")
    if rs:
        parts.append(f"SUBSTRING(CAST(`{rs}` AS STRING), 1, 320) AS response_preview")
    else:
        parts.append("CAST(NULL AS STRING) AS response_preview")

    cgroup_col = _comparison_sql_column(ctx["cols"], s.inference_comparison_group_column)
    if cgroup_col:
        parts.append(f"CAST(`{cgroup_col}` AS STRING) AS comparison_group_id")
    else:
        parts.append("CAST(NULL AS STRING) AS comparison_group_id")

    for opt in ("input_tokens", "output_tokens", "total_tokens"):
        c = col(opt)
        if c:
            parts.append(f"CAST(`{c}` AS DOUBLE) AS {opt}")
        else:
            parts.append(f"CAST(NULL AS DOUBLE) AS {opt}")

    sql = (
        f"SELECT {', '.join(parts)} FROM {tbl} "
        f"WHERE 1=1 {pred_st} {gm_extra} ORDER BY `{tc}` DESC NULLS LAST LIMIT {lim}"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
            colnames = [c[0] for c in cur.description] if cur.description else []
        traces = []

        def _trace_tok(v: Any) -> int | None:
            if v is None:
                return None
            try:
                return int(round(float(v)))
            except (TypeError, ValueError):
                return None

        for r in rows:
            rec = dict(zip(colnames, r))
            st = rec.get("status_code")
            lat = rec.get("latency_ms")
            try:
                st_i = int(st) if st is not None else None
            except (TypeError, ValueError):
                st_i = None
            try:
                lat_f = float(lat) if lat is not None else None
            except (TypeError, ValueError):
                lat_f = None
            ev = rec.get("event_time")

            cg = rec.get("comparison_group_id")
            traces.append(
                {
                    "request_id": str(rec["request_id"]) if rec.get("request_id") else None,
                    "event_time": ev.isoformat() if ev is not None and hasattr(ev, "isoformat") else str(ev),
                    "status_code": st_i,
                    "latency_ms": lat_f,
                    "destination_id": str(rec["destination_id"]) if rec.get("destination_id") is not None else None,
                    "url": str(rec["url"]) if rec.get("url") is not None else None,
                    "api_type": str(rec["api_type"]) if rec.get("api_type") is not None else None,
                    "requester": str(rec["requester"]) if rec.get("requester") is not None else None,
                    "request_preview": str(rec["request_preview"]) if rec.get("request_preview") else None,
                    "response_preview": str(rec["response_preview"]) if rec.get("response_preview") else None,
                    "comparison_group_id": str(cg) if cg else None,
                    "input_tokens": _trace_tok(rec.get("input_tokens")),
                    "output_tokens": _trace_tok(rec.get("output_tokens")),
                    "total_tokens": _trace_tok(rec.get("total_tokens")),
                }
            )
        out["traces"] = traces
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def _extract_reasoning_summary(response_text: str) -> str | None:
    if not response_text or len(response_text) < 10:
        return None
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return None
    # OpenAI-compatible / AI Gateway: choices[0].message.content may be string or structured blocks
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        if isinstance(msg, dict):
            reasoning = msg.get("reasoning")
            if isinstance(reasoning, dict):
                s = reasoning.get("summary") or reasoning.get("text")
                if isinstance(s, str):
                    return s[:4000]
            if isinstance(reasoning, str):
                return reasoning[:4000]
            content = msg.get("content")
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "reasoning":
                        summ = block.get("summary")
                        if isinstance(summ, list):
                            for item in summ:
                                if isinstance(item, dict) and item.get("type") == "summary_text":
                                    t = item.get("text")
                                    if isinstance(t, str):
                                        parts.append(t)
                        elif isinstance(summ, str):
                            parts.append(summ)
                if parts:
                    return "\n\n".join(parts)[:4000]
    return None


def trace_detail(request_id: str) -> dict[str, Any]:
    rid = (request_id or "").strip()
    if not rid or not re.match(r"^[a-zA-Z0-9_\-\.]+$", rid):
        return {"error": "invalid request_id", "request_id": request_id}

    ctx, err = _inference_table_ctx()
    if err or not ctx:
        return {"error": err or "no_context", "request_id": rid}
    if "request_id" not in ctx["cols"]:
        return {"error": "request_id column missing", "request_id": rid}

    tbl = ctx["table_sql"]
    rq_col = next(c for c in ctx["cols"] if c.lower() == "request_id")
    rid_sql = rid.replace("'", "''")
    sql = f"SELECT * FROM {tbl} WHERE `{rq_col}` = '{rid_sql}' LIMIT 1"
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
            col_names = [c[0] for c in cur.description] if cur.description else []
        if not row:
            return {"error": "not_found", "request_id": rid}
        record = {col_names[i]: row[i] for i in range(len(col_names))}
        req_raw = record.get("request")
        resp_raw = record.get("response")
        req_s = req_raw if isinstance(req_raw, str) else json.dumps(req_raw, default=str)[:8000]
        resp_s = resp_raw if isinstance(resp_raw, str) else json.dumps(resp_raw, default=str)[:8000]
        parsed_req: Any = None
        parsed_resp: Any = None
        try:
            parsed_req = json.loads(req_s) if req_s else None
        except json.JSONDecodeError:
            parsed_req = None
        try:
            parsed_resp = json.loads(resp_s) if resp_s else None
        except json.JSONDecodeError:
            parsed_resp = None

        reasoning = _extract_reasoning_summary(resp_s) if resp_s else None
        gw, gw_err = _fetch_ai_gateway_usage_row(rid)
        # "Lineage" of the call within the logged payload
        internal_lineage: list[dict[str, str]] = []
        internal_lineage.append({"step": "client → AI Gateway", "detail": str(record.get("url") or "chat/completions")})
        if record.get("destination_id"):
            internal_lineage.append(
                {"step": "routed model", "detail": str(record.get("destination_id"))},
            )
        mname = _response_model_name(parsed_resp)
        if mname:
            internal_lineage.append({"step": "model (response JSON)", "detail": mname})
        if record.get("api_type"):
            internal_lineage.append({"step": "API type", "detail": str(record.get("api_type"))})
        if gw and gw.get("total_tokens") is not None:
            internal_lineage.append(
                {
                    "step": "token usage (AI Gateway)",
                    "detail": (
                        f"in={gw.get('input_tokens')}, out={gw.get('output_tokens')}, "
                        f"total={gw.get('total_tokens')}"
                    ),
                },
            )
        elif gw_err:
            internal_lineage.append({"step": "AI Gateway usage row", "detail": f"not available: {gw_err}"})
        if reasoning:
            internal_lineage.append(
                {"step": "model reasoning (from response payload)", "detail": reasoning[:500] + ("…" if len(reasoning) > 500 else "")},
            )

        serializable = {str(k): _json_safe_value(v) for k, v in record.items()}
        cgroup_actual = _comparison_sql_column(ctx["cols"], get_settings().inference_comparison_group_column)
        raw_cg = record.get(cgroup_actual) if cgroup_actual else None
        comparison_group_id = str(raw_cg).strip() if raw_cg is not None and str(raw_cg).strip() else None
        lineage_graph = _build_request_lineage_graph(serializable, parsed_resp, gw, reasoning)

        return {
            "request_id": rid,
            "comparison_group_id": comparison_group_id,
            "record": serializable,
            "request_json": parsed_req,
            "response_json": parsed_resp,
            "reasoning_summary": reasoning,
            "internal_lineage": internal_lineage,
            "lineage_graph": lineage_graph,
            "ai_gateway_usage": gw,
            "ai_gateway_usage_error": gw_err,
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e).strip()[:500], "request_id": rid}


def governance_lineage(limit: int = 80) -> dict[str, Any]:
    """UC system table lineage when enabled; graph-friendly edges."""
    ctx, err = _inference_table_ctx()
    runtime = build_runtime_flow(ctx, days=7) if ctx else {"error": err, "routes": [], "models": []}
    out: dict[str, Any] = {
        "inference_table": None,
        "edges": [],
        "nodes": [],
        "error": err,
        "runtime_flow": runtime,
        "hint": (
            "Unity Catalog table lineage comes from system.access.table_lineage (separate from AI Gateway HTTP logs). "
            "Enable system tables + grant SELECT — see docs URLs below."
        ),
        "system_tables_doc_url": "https://docs.databricks.com/aws/en/admin/system-tables/",
        "lineage_system_table_doc_url": "https://docs.databricks.com/aws/en/admin/system-tables/lineage",
        "uc_lineage_query_error": None,
    }
    if err or not ctx:
        return out
    fqns: list[str] = list(ctx.get("fqns") or [ctx["fqn"]])
    out["inference_table_fqns"] = fqns
    out["inference_table_primary"] = fqns[0] if fqns else None
    out["inference_table_display"] = ctx.get("fqn_label") or (fqns[0] if fqns else None)
    out["inference_table"] = fqns[0] if fqns else None
    esc = ",".join("'" + f.replace("'", "''") + "'" for f in fqns)
    lim = max(1, min(500, int(limit)))
    sql = (
        "SELECT source_table_full_name, target_table_full_name, entity_type, created_by, event_time "
        "FROM system.access.table_lineage "
        f"WHERE source_table_full_name IN ({esc}) OR target_table_full_name IN ({esc}) "
        "ORDER BY event_time DESC NULLS LAST "
        f"LIMIT {lim}"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
        edges = []
        node_set: set[str] = set()
        for r in rows:
            src, tgt = r[0], r[1]
            et = str(r[2]) if r[2] else None
            cb = str(r[3]) if r[3] else None
            ev = r[4].isoformat() if r[4] is not None and hasattr(r[4], "isoformat") else str(r[4])
            s = str(src) if src else ""
            t = str(tgt) if tgt else ""
            if s:
                node_set.add(s)
            if t:
                node_set.add(t)
            edges.append(
                {
                    "source": s or None,
                    "target": t or None,
                    "entity_type": et,
                    "created_by": cb,
                    "event_time": ev,
                }
            )
        nodes = [{"id": n, "label": n.split(".")[-1], "fqn": n} for n in sorted(node_set)]
        out["edges"] = edges
        out["nodes"] = nodes
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["uc_lineage_query_error"] = str(e).strip()[:500]

    hub_nodes: dict[str, dict[str, Any]] = {}
    for n in out.get("nodes") or []:
        fqn = n.get("fqn")
        if isinstance(fqn, str) and fqn:
            hub_nodes[fqn] = n if isinstance(n, dict) else {"id": fqn, "label": fqn.split(".")[-1], "fqn": fqn}
    for f in fqns:
        if f not in hub_nodes:
            hub_nodes[f] = {"id": f, "label": f.split(".")[-1], "fqn": f}
    out["nodes"] = sorted(hub_nodes.values(), key=lambda x: str(x.get("fqn") or ""))

    # Recent UC lineage in this workspace (broader than inference FQN filter)
    out["workspace_lineage_recent"] = []
    out["workspace_lineage_note"] = (
        "Set DATABRICKS_WORKSPACE_ID in backend/.env to load a workspace-scoped sample from system.access.table_lineage."
    )
    wid_raw = get_settings().workspace_id.strip()
    wid = wid_raw if wid_raw.isdigit() else ""
    if wid:
        try:
            sql_w = (
                "SELECT source_table_full_name, target_table_full_name, entity_type, created_by, event_time "
                "FROM system.access.table_lineage "
                f"WHERE CAST(workspace_id AS STRING) = '{wid}' "
                "ORDER BY event_time DESC NULLS LAST LIMIT 25"
            )
            with sql_connection() as conn:
                cur = conn.cursor()
                cur.execute(sql_w)
                wrows = cur.fetchall() or []
            w_edges: list[dict[str, Any]] = []
            for r in wrows:
                src, tgt = r[0], r[1]
                et = str(r[2]) if r[2] else None
                cb = str(r[3]) if r[3] else None
                ev = r[4].isoformat() if r[4] is not None and hasattr(r[4], "isoformat") else str(r[4])
                w_edges.append(
                    {
                        "source": str(src) if src else None,
                        "target": str(tgt) if tgt else None,
                        "entity_type": et,
                        "created_by": cb,
                        "event_time": ev,
                    }
                )
            out["workspace_lineage_recent"] = w_edges
            out["workspace_lineage_note"] = (
                f"Latest {len(w_edges)} lineage events where workspace_id = {wid} "
                "(all UC tables — not only your inference log)."
            )
        except Exception as e:  # noqa: BLE001
            out["workspace_lineage_recent_error"] = str(e).strip()[:500]
    return out


def mlflow_system_overview(run_limit: int = 20) -> dict[str, Any]:
    """Summaries from system.mlflow.* (Unity Catalog system tables)."""
    s = get_settings()
    wid_raw = s.workspace_id.strip()
    wid = wid_raw if wid_raw.isdigit() else ""
    lim = max(1, min(100, int(run_limit)))
    out: dict[str, Any] = {
        "workspace_id_filter": wid or None,
        "experiments_count": None,
        "runs_count": None,
        "experiments_sample": [],
        "recent_runs": [],
        "error": None,
        "doc_url": "https://docs.databricks.com/aws/en/admin/system-tables/mlflow",
    }
    try:
        ws_clause = f"WHERE CAST(workspace_id AS STRING) = '{wid}'" if wid else ""
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM system.mlflow.experiments_latest {ws_clause}")  # noqa: S608
            out["experiments_count"] = int(cur.fetchone()[0] or 0)
            cur.execute(f"SELECT COUNT(*) FROM system.mlflow.runs_latest {ws_clause}")  # noqa: S608
            out["runs_count"] = int(cur.fetchone()[0] or 0)
            cur.execute(
                f"SELECT experiment_id, name FROM system.mlflow.experiments_latest {ws_clause} "  # noqa: S608
                f"ORDER BY create_time DESC NULLS LAST LIMIT 15"
            )
            for row in cur.fetchall() or []:
                out["experiments_sample"].append(
                    {"experiment_id": str(row[0]) if row[0] is not None else None, "name": str(row[1]) if row[1] else None},
                )
            cur.execute(
                f"SELECT run_id, experiment_id, run_name, status, start_time, created_by "
                f"FROM system.mlflow.runs_latest {ws_clause} "
                f"ORDER BY start_time DESC NULLS LAST LIMIT {lim}"
            )
            for row in cur.fetchall() or []:
                st = row[4]
                out["recent_runs"].append(
                    {
                        "run_id": str(row[0]) if row[0] else None,
                        "experiment_id": str(row[1]) if row[1] else None,
                        "run_name": str(row[2]) if row[2] else None,
                        "status": str(row[3]) if row[3] else None,
                        "start_time": st.isoformat() if st is not None and hasattr(st, "isoformat") else str(st),
                        "created_by": str(row[5]) if row[5] else None,
                    },
                )
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def governance_audit(limit: int = 40) -> dict[str, Any]:
    """Lightweight audit from inference rows (who called, when, outcome)."""
    ctx, err = _inference_table_ctx()
    out: dict[str, Any] = {"events": [], "error": err}
    if err or not ctx:
        return out
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    lim = max(1, min(200, int(limit)))
    has_req = "requester" in ctx["cols"]
    req_sel = "`requester`" if has_req else "CAST(NULL AS STRING)"
    sql = (
        f"SELECT `request_id`, `{tc}`, {req_sel} AS requester, `status_code`, `latency_ms`, `destination_id` "
        f"FROM {tbl} ORDER BY `{tc}` DESC NULLS LAST LIMIT {lim}"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
        out["events"] = []
        for r in rows:
            st = r[3]
            lat = r[4]
            try:
                st_i = int(st) if st is not None else None
            except (TypeError, ValueError):
                st_i = None
            try:
                lat_f = float(lat) if lat is not None else None
            except (TypeError, ValueError):
                lat_f = None
            out["events"].append(
                {
                    "request_id": str(r[0]) if r[0] else None,
                    "event_time": r[1].isoformat() if r[1] is not None and hasattr(r[1], "isoformat") else str(r[1]),
                    "requester": str(r[2]) if r[2] is not None else None,
                    "status_code": st_i,
                    "latency_ms": lat_f,
                    "destination_id": str(r[5]) if r[5] is not None else None,
                }
            )
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def quality_observability() -> dict[str, Any]:
    """Production quality proxies without MLflow (latency stability, errors, reasoning presence)."""
    ctx, err = _inference_table_ctx()
    out: dict[str, Any] = {
        "window_hours": 24,
        "avg_latency_ms": None,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "error_rate_pct": None,
        "requests_sampled_for_json": 0,
        "responses_with_reasoning_pct": None,
        "error": err,
    }
    if err or not ctx:
        return out
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    lc = ctx["latency_col"]
    sc = ctx["status_col"]
    if not lc:
        out["error"] = "latency column not found"
        return out
    try:
        err_sql = "0.0"
        if sc:
            err_sql = (
                "AVG(CASE WHEN CAST(`" + sc + "` AS DOUBLE) >= 400 "
                "OR CAST(`" + sc + "` AS DOUBLE) < 100 THEN 1.0 ELSE 0.0 END)"
            )
        sql = (
            f"SELECT AVG(`{lc}`), approx_percentile(`{lc}`, 0.5), approx_percentile(`{lc}`, 0.95), {err_sql} "
            f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL 24 HOURS"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
        if row:
            out["avg_latency_ms"] = float(row[0]) if row[0] is not None else None
            out["p50_latency_ms"] = float(row[1]) if row[1] is not None else None
            out["p95_latency_ms"] = float(row[2]) if row[2] is not None else None
            out["error_rate_pct"] = float(row[3]) * 100.0 if row[3] is not None else 0.0

        sql_sample = (
            f"SELECT CAST(`response` AS STRING) AS rsp FROM {tbl} "
            f"WHERE `{tc}` >= current_timestamp() - INTERVAL 24 HOURS "
            f"AND `response` IS NOT NULL LIMIT 50"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_sample)
            samples = cur.fetchall() or []
        with_reason = 0
        for (txt,) in samples:
            if not txt:
                continue
            if _extract_reasoning_summary(str(txt)):
                with_reason += 1
        out["requests_sampled_for_json"] = len(samples)
        if samples:
            out["responses_with_reasoning_pct"] = round(100.0 * with_reason / len(samples), 1)
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def quality_trend(days: int = 14) -> dict[str, Any]:
    """Daily avg latency + error rate for trend chart."""
    ctx, err = _inference_table_ctx()
    out: dict[str, Any] = {"days": int(days), "points": [], "error": err}
    if err or not ctx:
        return out
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    lc = ctx["latency_col"]
    sc = ctx["status_col"]
    if not lc:
        out["error"] = "latency column not found"
        return out
    err_case = "0.0"
    if sc:
        err_case = (
            "AVG(CASE WHEN CAST(`" + sc + "` AS DOUBLE) >= 400 "
            "OR CAST(`" + sc + "` AS DOUBLE) < 100 THEN 1.0 ELSE 0.0 END) * 100.0"
        )
    sql = (
        f"SELECT date_trunc('DAY', `{tc}`) AS d, AVG(`{lc}`), {err_case} AS err_pct "
        f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {int(days)} DAYS "
        f"GROUP BY 1 ORDER BY 1 ASC"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
        out["points"] = [
            {
                "day": r[0].isoformat()[:10] if r[0] is not None and hasattr(r[0], "isoformat") else str(r[0]),
                "avg_latency_ms": float(r[1]) if r[1] is not None else 0.0,
                "error_rate_pct": float(r[2]) if r[2] is not None else 0.0,
            }
            for r in rows
            if r and r[0] is not None
        ]
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out
