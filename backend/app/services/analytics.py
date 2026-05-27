"""Time-series, cost proxies, traces, and governance queries over inference + system tables."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from app.services.inference import (
    LATENCY_CANDIDATES,
    REQUEST_BODY_CANDIDATES,
    RESPONSE_BODY_CANDIDATES,
    STATUS_CANDIDATES,
)

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
    from app.services.compare_run import gateway_exclude_test_request_ids_clause

    excl24 = gateway_exclude_test_request_ids_clause(24)
    excl7d = gateway_exclude_test_request_ids_clause(24 * 7)
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
                f"WHERE event_time >= current_timestamp() - INTERVAL 24 HOURS{w}{excl24}"
            )
            r24 = cur.fetchone()
            cur.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM system.ai_gateway.usage "
                f"WHERE event_time >= current_timestamp() - INTERVAL 7 DAYS{w}{excl7d}"
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


def _usage_tokens_from_completion_json(parsed: Any) -> dict[str, Any] | None:
    """Token counts from an OpenAI-style completion body (or flattened Anthropic-ish fields)."""
    if not isinstance(parsed, dict):
        return None
    u = parsed.get("usage")
    inp = out_t = tot = None
    if isinstance(u, dict):
        inp = u.get("prompt_tokens")
        if inp is None:
            inp = u.get("input_tokens")
        out_t = u.get("completion_tokens")
        if out_t is None:
            out_t = u.get("output_tokens")
        tot = u.get("total_tokens")
    if tot is None and inp is None and out_t is None:
        inp = parsed.get("input_tokens")
        out_t = parsed.get("output_tokens")
        tot = parsed.get("total_tokens")
    if inp is None and parsed.get("usage_metadata") is not None and isinstance(parsed.get("usage_metadata"), dict):
        um = parsed["usage_metadata"]
        inp = um.get("prompt_token_count") or um.get("input_tokens")
        out_t = um.get("candidates_token_count") or um.get("output_tokens")
        tot = um.get("total_token_count") or um.get("total_tokens")
    if tot is None and inp is not None and out_t is not None:
        try:
            tot = int(inp) + int(out_t)
        except (TypeError, ValueError):
            tot = None
    if tot is None:
        return None
    return {"input_tokens": inp, "output_tokens": out_t, "total_tokens": tot}


def _usage_tokens_deep(parsed: Any, *, depth: int = 0) -> dict[str, Any] | None:
    """Walk common wrapper shapes until we find usable token counts (Agents / Gateway sometimes nest responses)."""
    if depth > 8 or parsed is None:
        return None
    if isinstance(parsed, str):
        try:
            return _usage_tokens_deep(json.loads(parsed), depth=depth + 1)
        except json.JSONDecodeError:
            return None
    hit = _usage_tokens_from_completion_json(parsed)
    if hit:
        return hit
    if not isinstance(parsed, dict):
        return None
    for key in ("response", "result", "data", "message", "output", "completion", "body"):
        nested = parsed.get(key)
        if nested is None:
            continue
        hit = _usage_tokens_deep(nested, depth=depth + 1)
        if hit:
            return hit
    ch = parsed.get("choices")
    if isinstance(ch, list):
        for item in ch[:3]:
            if isinstance(item, dict):
                hit = _usage_tokens_deep(item.get("message") or item.get("delta"), depth=depth + 1)
                if hit:
                    return hit
    return None


def _gateway_request_id_candidates(primary: str, parsed_req: Any, parsed_resp: Any) -> list[str]:
    """Collect ids that might appear as system.ai_gateway.usage.request_id (often differs from inference table id for Apps / Agents SDK)."""
    out: list[str] = []
    seen: set[str] = set()

    def add(x: Any) -> None:
        if not isinstance(x, str):
            return
        s = x.strip()
        if not s or not re.match(r"^[a-zA-Z0-9_\-\.]+$", s):
            return
        if s in seen:
            return
        seen.add(s)
        out.append(s)

    add(primary)

    if isinstance(parsed_resp, dict):
        add(parsed_resp.get("id"))
        for nest_key in ("response", "result"):
            nest = parsed_resp.get(nest_key)
            if isinstance(nest, dict) and isinstance(nest.get("id"), str):
                add(nest.get("id"))
    if isinstance(parsed_req, dict):
        if isinstance(parsed_req.get("request_id"), str):
            add(parsed_req.get("request_id"))
        if isinstance(parsed_req.get("client_request_id"), str):
            add(parsed_req.get("client_request_id"))
        for oid in ("id", "invocation_id", "generation_id", "dashscope_request_id"):
            if isinstance(parsed_req.get(oid), str):
                add(parsed_req.get(oid))
        for meta_k in ("metadata", "extra_headers", "extra_params", "headers"):
            meta = parsed_req.get(meta_k)
            if isinstance(meta, dict):
                for mk in (
                    "request_id",
                    "client_request_id",
                    "databricks_request_id",
                    "trace_id",
                    "invocation_id",
                    "openai-request-id",
                    "x-request-id",
                ):
                    if isinstance(meta.get(mk), str):
                        add(meta.get(mk))
    return out[:16]


def _fetch_ai_gateway_usage_first_match(candidates: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    last_err: str | None = None
    for cand in candidates:
        row, err = _fetch_ai_gateway_usage_row(cand)
        if err:
            last_err = err
        if row:
            return row, None
    return None, last_err


def _fetch_ai_gateway_usage_heuristic(
    record: dict[str, Any],
    time_col: str,
    id_candidates: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    """When request_id cannot be joined, match gateway rows by timestamp + destination_id (case-insensitive; widened window)."""
    ev = record.get(time_col)
    if ev is None or not str(time_col).strip():
        return None, None
    if hasattr(ev, "isoformat"):
        try:
            ev_lit = ev.isoformat(sep=" ", timespec="seconds")
        except Exception:  # noqa: BLE001
            ev_lit = str(ev)
    else:
        ev_lit = str(ev)
    ev_esc = ev_lit.replace("'", "''")
    dest = record.get("destination_id")
    if dest is None or not str(dest).strip():
        return None, None
    dest_esc = str(dest).strip().replace("'", "''")
    order_lat = ""
    inf_lat = record.get("latency_ms")
    if inf_lat is not None:
        try:
            lf = float(inf_lat)
            order_lat = f"ABS(COALESCE(CAST(latency_ms AS DOUBLE), 1e12) - {lf}) ASC NULLS LAST, "
        except (TypeError, ValueError):
            pass
    w = _ai_gateway_workspace_sql()
    rows: list[Any] = []
    colnames: list[str] = []
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            for win in (10, 30):
                sql = (
                    "SELECT request_id, event_time, latency_ms, status_code, "
                    "CAST(destination_id AS STRING) AS destination_id, destination_model, destination_name, "
                    "input_tokens, output_tokens, total_tokens, url, requester, api_type "
                    "FROM system.ai_gateway.usage WHERE 1=1 "
                    f"{w}"
                    f" AND event_time >= CAST('{ev_esc}' AS TIMESTAMP) - INTERVAL {win} MINUTES "
                    f" AND event_time <= CAST('{ev_esc}' AS TIMESTAMP) + INTERVAL {win} MINUTES "
                    " AND LOWER(TRIM(CAST(destination_id AS STRING))) = "
                    f"LOWER(TRIM('{dest_esc}')) "
                    f"ORDER BY {order_lat}event_time DESC "
                    "LIMIT 25"
                )
                cur.execute(sql)
                rows = cur.fetchall() or []
                colnames = [c[0] for c in cur.description] if cur.description else []
                if rows:
                    break
    except Exception as e:  # noqa: BLE001
        return None, str(e).strip()[:500]
    if not rows:
        return None, None
    cand_set = {c for c in id_candidates if c}
    best: dict[str, Any] | None = None
    for row in rows:
        rec = {colnames[i]: row[i] for i in range(len(colnames))}
        rid_gw = str(rec.get("request_id") or "")
        if rid_gw and rid_gw in cand_set:
            best = rec
            break
    if best is None:
        for row in rows:
            rec = {colnames[i]: row[i] for i in range(len(colnames))}
            tt = rec.get("total_tokens")
            try:
                if tt is not None and int(tt) > 0:
                    best = rec
                    break
            except (TypeError, ValueError):
                continue
        if best is None:
            best = {colnames[i]: rows[0][i] for i in range(len(colnames))}
    return {str(k): _json_safe_value(v) for k, v in best.items()}, None


def resolve_ai_gateway_metering(
    ctx: dict[str, Any] | None,
    inference_request_id: str,
    record: dict[str, Any] | None,
    parsed_resp: Any,
    parsed_req: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve AI Gateway metering for one inference row: exact id chain → heuristic → completion usage JSON."""
    rid = (inference_request_id or "").strip()
    if not rid:
        return None, None
    candidates = _gateway_request_id_candidates(rid, parsed_req, parsed_resp)
    gw, err = _fetch_ai_gateway_usage_first_match(candidates)
    if gw:
        gw2 = dict(gw)
        if gw2.get("metering_source") is None:
            gw2["metering_source"] = "ai_gateway_exact_request_id"
        return gw2, err

    if record is not None and ctx is not None:
        tc = ctx.get("time_col")
        if tc and isinstance(tc, str):
            gw3, herr = _fetch_ai_gateway_usage_heuristic(record, tc, candidates)
            if gw3:
                gw3.setdefault("metering_source", "ai_gateway_heuristic_time_destination")
                return gw3, herr

    usage = _usage_tokens_deep(parsed_resp)
    if usage and usage.get("total_tokens") is not None:
        try:
            tot = int(usage["total_tokens"])
        except (TypeError, ValueError):
            tot = 0
        if tot > 0:
            tin = usage.get("input_tokens")
            tout = usage.get("output_tokens")
            try:
                tin_i = int(float(tin)) if tin is not None else None
            except (TypeError, ValueError):
                tin_i = None
            try:
                tout_i = int(float(tout)) if tout is not None else None
            except (TypeError, ValueError):
                tout_i = None
            return (
                {
                    "request_id": rid,
                    "input_tokens": tin_i,
                    "output_tokens": tout_i,
                    "total_tokens": tot,
                    "metering_source": "completion_response_json",
                    "requester": record.get("requester") if isinstance(record, dict) else None,
                },
                None,
            )
    return None, err


def _fetch_inference_record_and_parsed(ctx: dict[str, Any], rid: str) -> tuple[dict[str, Any] | None, Any, Any]:
    """Load one inference payload row + parsed bodies (for cost rollup when gateway IN filter misses)."""
    cmap = {c.lower(): c for c in ctx["cols"]}
    if "request_id" not in cmap:
        return None, None, None
    tbl = ctx["table_sql"]
    rq_col = cmap["request_id"]
    esc = rid.replace("'", "''")
    sql = f"SELECT * FROM {tbl} WHERE CAST(`{rq_col}` AS STRING) = '{esc}' LIMIT 1"
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
            col_names = [c[0] for c in cur.description] if cur.description else []
    except Exception:  # noqa: BLE001
        return None, None, None
    if not row:
        return None, None, None
    record = {col_names[i]: row[i] for i in range(len(col_names))}
    avail_row = set(col_names)
    req_pick = ctx.get("request_body_col") or pick_col(avail_row, REQUEST_BODY_CANDIDATES)
    rsp_pick = ctx.get("response_body_col") or pick_col(avail_row, RESPONSE_BODY_CANDIDATES)
    req_raw = record.get(req_pick) if req_pick else None
    resp_raw = record.get(rsp_pick) if rsp_pick else None
    if req_raw is None:
        for cand in REQUEST_BODY_CANDIDATES:
            hit = next((c for c in col_names if c.lower() == cand.lower()), None)
            if hit:
                req_raw = record.get(hit)
                break
    if resp_raw is None:
        for cand in RESPONSE_BODY_CANDIDATES:
            hit = next((c for c in col_names if c.lower() == cand.lower()), None)
            if hit:
                resp_raw = record.get(hit)
                break
    parsed_req, parsed_resp, _d1, _d2 = _parse_trace_payloads(req_raw, resp_raw)
    return record, parsed_req, parsed_resp


def _enrich_gateway_rollup_for_pinned_requests(
    ag: dict[str, Any] | None,
    ctx: dict[str, Any] | None,
    rid_list: list[str],
    row_cache: dict[str, tuple[dict[str, Any] | None, Any, Any]],
) -> dict[str, Any]:
    """When system.ai_gateway.usage does not match inference request_id, still surface tokens for pinned Cost view."""
    if not rid_list or not ctx:
        return ag if ag is not None else {}
    base = dict(ag or {})
    cur_tok = int(base.get("total_tokens") or 0)
    if cur_tok > 0:
        return base
    missing = [p for p in rid_list if p not in row_cache]

    def fetch_pair(pid: str) -> tuple[str, tuple[dict[str, Any] | None, Any, Any]]:
        return pid, _fetch_inference_record_and_parsed(ctx, pid)

    if len(missing) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as ex:
            for pid, tup in ex.map(fetch_pair, missing):
                row_cache[pid] = tup
    else:
        for pid in missing:
            row_cache[pid] = _fetch_inference_record_and_parsed(ctx, pid)

    def meter(pid: str) -> dict[str, Any] | None:
        rec, pq, ps = row_cache[pid]
        gwm, _ = resolve_ai_gateway_metering(ctx, pid, rec, ps, pq)
        return gwm

    if len(rid_list) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(rid_list))) as ex:
            gwm_list = list(ex.map(meter, rid_list))
    else:
        gwm_list = [meter(rid_list[0])]

    sum_tot = sum_in = sum_out = 0
    n_hit = 0
    for gwm in gwm_list:
        if not gwm or gwm.get("total_tokens") is None:
            continue
        try:
            tt = int(gwm["total_tokens"])
        except (TypeError, ValueError):
            continue
        if tt <= 0:
            continue
        sum_tot += tt
        n_hit += 1
        try:
            sum_in += int(float(gwm.get("input_tokens") or 0))
        except (TypeError, ValueError):
            pass
        try:
            sum_out += int(float(gwm.get("output_tokens") or 0))
        except (TypeError, ValueError):
            pass
    if sum_tot <= 0:
        return base
    base["total_tokens"] = sum_tot
    base["total_input_tokens"] = sum_in
    base["total_output_tokens"] = sum_out
    base["total_requests"] = max(int(base.get("total_requests") or 0), n_hit, len(rid_list))
    prev = str(base.get("note") or "").strip()
    hint = (
        "Tokens resolved via alternate gateway request_id, time/destination match, or completion `usage` "
        "(agent traffic often logs a different request_id than system.ai_gateway.usage)."
    )
    base["note"] = f"{prev} {hint}".strip() if prev else hint
    return base


_OAUTH_PRINCIPAL_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _caller_hint_from_chat_request_json(parsed_req: Any) -> str | None:
    """Prefer OpenAI-compatible `user` + common metadata keys if the agent logs the full chat request."""
    if not isinstance(parsed_req, dict):
        return None
    u = parsed_req.get("user")
    if isinstance(u, str) and u.strip():
        return u.strip()[:260]
    for k in ("submitted_by", "end_user", "actor_email", "user_email", "email"):
        v = parsed_req.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:260]
    meta = parsed_req.get("metadata")
    if not isinstance(meta, dict):
        meta = parsed_req.get("extra_headers")
    if isinstance(meta, dict):
        for mk in ("display_name", "name", "user_name", "user_email", "email", "user_id", "sub"):
            mv = meta.get(mk)
            if isinstance(mv, str) and mv.strip():
                return mv.strip()[:260]
    return None


def _caller_display_from_record(record: dict[str, Any], requester: str) -> str:
    """Prefer human-ish columns when present; clarify Databricks principal UUIDs (Apps / OAuth)."""
    lr = {str(k).lower(): k for k in record}
    for want in ("user_name", "user_email", "created_by", "principal_name", "display_name", "actor_name"):
        orig = lr.get(want)
        if not orig:
            continue
        val = record.get(orig)
        if val is not None and str(val).strip():
            label = str(val).strip()
            if requester and label.lower() != requester.lower():
                return f"{label}\n({requester})"
            return label
    rq = (requester or "").strip()
    if rq and _OAUTH_PRINCIPAL_UUID_RE.fullmatch(rq):
        return f"Databricks OAuth / Apps identity\n(id {rq})"
    return requester


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
    wpred = f"CAST(workspace_id AS STRING) = '{ws.replace(chr(39), chr(39) + chr(39))}'"
    ep = _billing_endpoint_name_expr()
    sql_agg = (
        "SELECT sku_name, "
        f"{ep} AS endpoint_name, "
        "CAST(SUM(usage_quantity) AS DOUBLE) AS dbu "
        "FROM system.billing.usage "
        f"WHERE {wpred} "
        f"AND usage_start_time >= CAST('{e0}' AS TIMESTAMP) "
        f"AND usage_start_time <= CAST('{e1}' AS TIMESTAMP) "
        f"{_billing_model_serving_where_sql()} "
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
        price_map = _fetch_billing_list_prices(uniq)
        by_ep, total_dbu, total_usd = _rollup_billing_usage_rows(agg_rows, price_map)
        out["total_dbu"] = total_dbu
        out["total_list_usd"] = round(total_usd, 6)
        out["pricing_partial"] = any(x.get("usd_per_dbu") is None for x in by_ep)
        if out["pricing_partial"]:
            out["note"] += " Partial: missing list price for some SKUs."
        out["by_endpoint"] = by_ep
        out["usage_source"] = "system.billing.usage"
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def _fetch_billing_list_prices(skus: list[str]) -> dict[str, tuple[float | None, str]]:
    if not skus:
        return {}
    escaped = ",".join("'" + s.replace("'", "''") + "'" for s in skus)
    sql_prices = (
        "SELECT sku_name, currency_code, pricing FROM system.billing.list_prices "
        f"WHERE sku_name IN ({escaped}) "
        "AND price_start_time <= current_timestamp() "
        "AND (price_end_time IS NULL OR price_end_time > current_timestamp())"
    )
    price_map: dict[str, tuple[float | None, str]] = {}
    try:
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
    except Exception:  # noqa: BLE001
        sql_fallback = (
            f"SELECT sku_name, currency_code, pricing FROM system.billing.list_prices "
            f"WHERE sku_name IN ({escaped})"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_fallback)
            for prow in cur.fetchall() or []:
                if not prow:
                    continue
                sku = str(prow[0])
                ccy = str(prow[1] or "USD")
                usd = _usd_per_dbu_from_pricing_json(prow[2])
                price_map[sku] = (usd, ccy)
    return price_map


def _rollup_billing_usage_rows(
    agg_rows: list[Any],
    price_map: dict[str, tuple[float | None, str]],
) -> tuple[list[dict[str, Any]], float, float]:
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
    return by_ep, total_dbu, total_usd


def _apply_billing_gateway_token_fallback(
    bill: dict[str, Any],
    *,
    gateway_total_tokens: int,
) -> dict[str, Any]:
    """When system.billing.usage has no serving rows, show $ from gateway token metering."""
    if bill.get("error"):
        return bill
    cur_usd = float(bill.get("total_list_usd") or 0)
    if cur_usd > 0 or gateway_total_tokens <= 0:
        return bill
    try:
        from app.services.compare_run import cost_estimate_meta

        meta = cost_estimate_meta()
        per_1m = meta.get("usd_per_1m_tokens")
        if per_1m is None:
            return bill
        est = round(gateway_total_tokens * float(per_1m) / 1_000_000.0, 6)
        if est <= 0:
            return bill
        bill = dict(bill)
        bill["total_list_usd"] = est
        bill["total_list_usd_billing_table"] = 0.0
        bill["attribution"] = "ai_gateway_token_estimate"
        bill["usage_source"] = "system.ai_gateway.usage"
        bill["note"] = (
            (bill.get("note") or "")
            + " No MODEL_SERVING/AI_GATEWAY DBU rows in system.billing.usage for this workspace/window yet. "
            "List price above is gateway total_tokens × AGENTOPS_COMPARE_USD_PER_1M_TOKENS (same as replay/compare). "
            "For Databricks-hosted routes, billing.usage normally records usage_unit=DBU under MODEL_SERVING or AI_GATEWAY "
            "with usage_metadata.ai_gateway.* — check Cost again after more traffic or a wider time range."
        ).strip()
    except Exception:  # noqa: BLE001
        pass
    return bill


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
        f"{_ctx_test_excl(ctx)} ORDER BY `{tc}` ASC NULLS LAST LIMIT 64"
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
    enriched_gw: dict[str, dict[str, Any]] = {}
    for rid in request_ids:
        row = dict(gw_map.get(rid) or {})
        if not row.get("total_tokens"):
            rec, pq, ps = _fetch_inference_record_and_parsed(ctx, rid)
            gwm, _ = resolve_ai_gateway_metering(ctx, rid, rec, ps, pq)
            if gwm:
                row = {**row, **gwm}
        enriched_gw[rid] = row

    billing = billing_model_serving_between(tmin, tmax) if tmin is not None and tmax is not None else {}

    sum_tok = 0
    for rid in request_ids:
        u = enriched_gw[rid]
        if u.get("total_tokens") is not None:
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
        gw = enriched_gw.get(rid) if rid else None
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


def _billing_workspace_sql() -> str:
    """Workspace filter for system.billing.usage (workspace_id is STRING in UC)."""
    ws = get_settings().workspace_id.strip()
    if not ws:
        return ""
    esc = ws.replace("'", "''")
    return f" AND CAST(workspace_id AS STRING) = '{esc}' "


def _billing_endpoint_name_expr() -> str:
    """Endpoint label for Unity AI Gateway + classic model serving (per Databricks billing schema)."""
    return (
        "COALESCE("
        "NULLIF(TRIM(CAST(usage_metadata.ai_gateway.endpoint_name AS STRING)), ''), "
        "NULLIF(TRIM(CAST(usage_metadata.endpoint_name AS STRING)), ''), "
        "NULLIF(TRIM(CAST(usage_metadata.ai_gateway.destination_model AS STRING)), ''), "
        "'(none)')"
    )


def _billing_model_serving_where_sql() -> str:
    """Match Databricks model-serving + AI Gateway billing rows (not usage_type=TOKEN only)."""
    return (
        " AND billing_origin_product IN ('MODEL_SERVING', 'AI_GATEWAY') "
        " AND (usage_unit = 'DBU' OR usage_type = 'TOKEN' "
        " OR sku_name LIKE '%SERVERLESS_REAL_TIME_INFERENCE%') "
    )


def _sql_billing_exact_for_request_ids(request_ids: list[str]) -> str:
    """Pin billing to endpoints tied to specific AI Gateway request_id rows (not fuzzy token OR)."""
    if not request_ids:
        return " AND 1=0 "
    uniq = []
    seen_r: set[str] = set()
    for rid in request_ids[:32]:
        r = str(rid or "").strip()
        if r and r not in seen_r:
            seen_r.add(r)
            uniq.append(r)
    if not uniq:
        return " AND 1=0 "
    gw_map, _gw_err = _fetch_ai_gateway_usage_batch(uniq, limit=len(uniq))
    ep = f"LOWER({_billing_endpoint_name_expr()})"
    parts: list[str] = []
    seen: set[str] = set()
    for rid in uniq:
        gw_row = gw_map.get(rid) or gw_map.get(str(rid))
        if not gw_row:
            continue
        labels: list[str] = []
        for k in ("destination_model", "destination_name", "destination_id"):
            v = gw_row.get(k)
            if v and str(v).strip():
                labels.append(str(v).strip())
        from app.services.agent_unify import gateway_route_slug_from_label

        slug = gateway_route_slug_from_label(
            str(gw_row.get("destination_model") or gw_row.get("destination_name") or ""),
        )
        if slug:
            labels.append(slug)
            labels.append(f"databricks-{slug}")
            labels.append(slug.replace("_", "-"))
        for lab in _collect_billing_endpoint_match_terms(*labels) or labels:
            key = lab.lower()
            if key in seen:
                continue
            seen.add(key)
            esc = key.replace("'", "''")
            parts.append(f"{ep} LIKE LOWER(CONCAT('%', '{esc}', '%'))")
    if not parts:
        return " AND 1=0 "
    return " AND (" + " OR ".join(parts) + ") "


def _billing_terms_from_inference_fqns(fqns: list[str]) -> list[str]:
    """Map UC payload table names to billing endpoint_name fragments."""
    from app.services.agent_unify import inference_table_to_route_name

    suffix = get_settings().inference_table_name_suffix or "_payload"
    raw_labels: list[str] = []
    for f in fqns:
        short = f.split(".")[-1] if f else ""
        route = inference_table_to_route_name(short, suffix)
        if route:
            raw_labels.append(route)
            raw_labels.append(route.replace("_", "-"))
            raw_labels.append(f"databricks-{route.replace('_', '-')}")
    return _collect_billing_endpoint_match_terms(*raw_labels)


def _sql_billing_endpoint_like_clause(terms: list[str]) -> str:
    """Extra WHERE fragment on gateway/serving endpoint fields."""
    if not terms:
        return " AND 1=0 "
    ep_expr = f"LOWER({_billing_endpoint_name_expr()})"
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


def _resolve_billing_terms_for_pinned_request(
    ctx: dict[str, Any] | None,
    rid: str,
    *,
    row_cache: dict[str, tuple[dict[str, Any] | None, Any, Any]] | None = None,
) -> list[str]:
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
    if row_cache is not None and rid in row_cache:
        _rec, _pq, parsed_resp = row_cache[rid]
        if isinstance(parsed_resp, dict):
            mname = _response_model_name(parsed_resp)
            if mname:
                return _collect_billing_endpoint_match_terms(mname)
    if not ctx:
        return []
    cmap = {c.lower(): c for c in ctx["cols"]}
    if "request_id" not in cmap:
        return []
    rsp_key = (ctx.get("response_body_col") or "").lower()
    if rsp_key not in cmap:
        rsp_key = ""
        for cand in RESPONSE_BODY_CANDIDATES:
            if cand.lower() in cmap:
                rsp_key = cand.lower()
                break
        if not rsp_key:
            return []
    tbl = ctx["table_sql"]
    rq = cmap["request_id"]
    rsp = cmap[rsp_key]
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


def _billing_workspace_diagnostic(hours: int) -> dict[str, Any] | None:
    """When list price is zero, show model-serving vs all billing products for the workspace."""
    ws = get_settings().workspace_id.strip()
    h = max(1, min(24 * 90, int(hours)))
    out: dict[str, Any] = {
        "configured_workspace_id": ws or None,
        "top_workspaces": [],
        "model_serving_products": [],
        "products_for_configured_workspace": [],
    }
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT CAST(workspace_id AS STRING) AS wid, "
                "COALESCE(billing_origin_product, '(none)') AS prod, "
                "CAST(SUM(usage_quantity) AS DOUBLE) AS q "
                "FROM system.billing.usage "
                f"WHERE usage_start_time >= current_timestamp() - INTERVAL {h} HOURS "
                f"{_billing_model_serving_where_sql()} "
                "GROUP BY 1, 2 ORDER BY q DESC NULLS LAST LIMIT 15"
            )
            for row in cur.fetchall() or []:
                if not row:
                    continue
                out["top_workspaces"].append(
                    {
                        "workspace_id": str(row[0]),
                        "billing_origin_product": str(row[1]),
                        "usage_quantity": float(row[2] or 0),
                    },
                )
            if ws:
                ws_esc = ws.replace("'", "''")
                cur.execute(
                    "SELECT COALESCE(billing_origin_product, '(none)') AS prod, "
                    "CAST(SUM(usage_quantity) AS DOUBLE) AS q "
                    "FROM system.billing.usage "
                    f"WHERE CAST(workspace_id AS STRING) = '{ws_esc}' "
                    f"AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS "
                    f"{_billing_model_serving_where_sql()} "
                    "GROUP BY 1 ORDER BY q DESC NULLS LAST LIMIT 10"
                )
                for row in cur.fetchall() or []:
                    if not row:
                        continue
                    out["model_serving_products"].append(
                        {
                            "billing_origin_product": str(row[0]),
                            "usage_quantity": float(row[1] or 0),
                        },
                    )
                cur.execute(
                    "SELECT COALESCE(billing_origin_product, '(none)') AS prod, "
                    "CAST(SUM(usage_quantity) AS DOUBLE) AS q "
                    "FROM system.billing.usage "
                    f"WHERE CAST(workspace_id AS STRING) = '{ws_esc}' "
                    f"AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS "
                    "GROUP BY 1 ORDER BY q DESC NULLS LAST LIMIT 10"
                )
                for row in cur.fetchall() or []:
                    if not row:
                        continue
                    out["products_for_configured_workspace"].append(
                        {
                            "billing_origin_product": str(row[0]),
                            "usage_quantity": float(row[1] or 0),
                        },
                    )
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:400]
    if (
        not out["top_workspaces"]
        and not out["products_for_configured_workspace"]
        and not out["model_serving_products"]
    ):
        return out if out.get("error") else None
    return out


def billing_model_serving_cost(
    hours: int,
    *,
    endpoint_match_terms: list[str] | None = None,
    endpoint_exact_request_ids: list[str] | None = None,
    gateway_total_tokens: int | None = None,
) -> dict[str, Any]:
    """List-price USD from system.billing.usage (MODEL_SERVING / AI_GATEWAY, DBU) × list_prices.

    endpoint_match_terms:
      None  — workspace-wide (all MODEL_SERVING endpoints in window).
      []    — caller scoped cost but no endpoint tokens derived → return zeros (no false workspace rollup).
      [...] — OR of LIKE filters on gateway/serving endpoint fields.
    endpoint_exact_request_ids:
      Pinned request_id(s) — use gateway row endpoint(s) only (avoids fuzzy match across all models).
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
            "Estimate: system.billing.usage (MODEL_SERVING / AI_GATEWAY, usage_unit=DBU per Databricks docs) "
            "× effective_list in system.billing.list_prices. Not an invoice."
        ),
        "usage_source": None,
    }
    if not ws:
        out["error"] = "set DATABRICKS_WORKSPACE_ID for billing.usage"
        return out
    ws_esc = ws.replace("'", "''")
    wpred = f"CAST(workspace_id AS STRING) = '{ws_esc}'"
    rid_exact = [str(r).strip() for r in (endpoint_exact_request_ids or []) if r and str(r).strip()]
    if rid_exact:
        ep_extra = _sql_billing_exact_for_request_ids(rid_exact)
        scoped = True
        out["attribution"] = "request_pinned"
        out["endpoint_match_terms"] = None
    elif scoped:
        ep_extra = _sql_billing_endpoint_like_clause(endpoint_match_terms or [])
    else:
        ep_extra = ""
    ep = _billing_endpoint_name_expr()
    sql_agg = (
        "SELECT sku_name, "
        f"{ep} AS endpoint_name, "
        "CAST(SUM(usage_quantity) AS DOUBLE) AS dbu "
        "FROM system.billing.usage "
        f"WHERE {wpred} "
        f"AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS "
        f"{_billing_model_serving_where_sql()} "
        f"{ep_extra}"
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
            diag = _billing_workspace_diagnostic(h)
            if diag:
                out["diagnostic"] = diag
            if scoped:
                if endpoint_match_terms:
                    out["note"] += (
                        " Scoped filter matched no rows in system.billing.usage "
                        "(try clearing scope; gateway endpoint names may differ from billing)."
                    )
                else:
                    out["note"] += (
                        " Cost scope active but no endpoint tokens derived for billing match."
                    )
            else:
                prods = (diag or {}).get("products_for_configured_workspace") or []
                prod_names = ", ".join(
                    str(x.get("billing_origin_product")) for x in prods[:8]
                ) or "(none in window)"
                out["note"] += (
                    f" No MODEL_SERVING/AI_GATEWAY DBU rows in system.billing.usage for workspace {ws} "
                    f"in the last {h}h (other billing products in window: {prod_names}). "
                    "Databricks-hosted gateway traffic still appears in system.ai_gateway.usage; "
                    "list price may show a token-based estimate until billing.usage catches up."
                )
            if gateway_total_tokens and gateway_total_tokens > 0:
                out = _apply_billing_gateway_token_fallback(
                    out, gateway_total_tokens=gateway_total_tokens
                )
            return out

        skus = [str(r[0]) for r in agg_rows if r and r[0] is not None]
        price_map = _fetch_billing_list_prices(sorted(set(skus)))
        by_ep, total_dbu, total_usd = _rollup_billing_usage_rows(agg_rows, price_map)
        out["total_dbu"] = total_dbu
        out["total_list_usd"] = round(total_usd, 6)
        out["pricing_partial"] = any(x.get("usd_per_dbu") is None for x in by_ep)
        if out["pricing_partial"]:
            out["note"] += " Partial: missing list price for some SKUs."
        if scoped and endpoint_match_terms:
            out["attribution"] = "endpoint_filtered"
            out["note"] += (
                " Filtered by gateway/serving endpoint fields in usage_metadata "
                "(ai_gateway.endpoint_name, endpoint_name, destination_model)."
            )
        out["by_endpoint"] = by_ep
        out["usage_source"] = "system.billing.usage"
        if (out.get("total_list_usd") or 0) == 0 and gateway_total_tokens and gateway_total_tokens > 0:
            out = _apply_billing_gateway_token_fallback(
                out, gateway_total_tokens=gateway_total_tokens
            )
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


def _gateway_model_label_sql() -> str:
    return (
        "COALESCE(NULLIF(TRIM(CAST(destination_model AS STRING)), ''), "
        "NULLIF(TRIM(CAST(destination_name AS STRING)), ''), "
        "CAST(destination_id AS STRING), 'unknown')"
    )


def _gateway_usage_where_clause(hours: int, gateway_models: list[str] | None = None) -> str:
    h = max(1, min(24 * 90, int(hours)))
    w = _ai_gateway_workspace_sql()
    gm = _gateway_models_sql_fragment(gateway_models) if gateway_models else ""
    from app.services.compare_run import gateway_exclude_test_request_ids_clause

    excl = gateway_exclude_test_request_ids_clause(h)
    return (
        f"WHERE event_time >= current_timestamp() - INTERVAL {h} HOURS"
        f"{w}{gm}{excl}"
    )


def gateway_agent_rollups(
    limit: int = 50,
    hours: int = 24,
    gateway_models: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Per-model RPM / p95 / error rate from system.ai_gateway.usage."""
    lim = max(1, min(100, int(limit)))
    where = _gateway_usage_where_clause(hours, gateway_models)
    m_expr = _gateway_model_label_sql()
    sql = (
        f"SELECT {m_expr} AS m, COUNT(*) AS cnt, "
        f"approx_percentile(latency_ms, 0.95) AS p95, "
        "AVG(CASE WHEN CAST(status_code AS DOUBLE) >= 400 "
        "OR CAST(status_code AS DOUBLE) < 100 THEN 1.0 ELSE 0.0 END) AS err_frac "
        f"FROM system.ai_gateway.usage {where} "
        f"GROUP BY 1 ORDER BY cnt DESC NULLS LAST LIMIT {lim}"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r or r[0] is None:
            continue
        k = str(r[0])
        cnt = int(r[1] or 0)
        out.append(
            {
                "id": k[:128],
                "name": k[:128] or "unknown",
                "requests_24h": cnt,
                "rpm": cnt / (24.0 * 60.0) if hours >= 24 else cnt / (max(1, hours) * 60.0),
                "p95_latency_ms": float(r[2]) if r[2] is not None else 0.0,
                "error_rate_pct": float(r[3]) * 100.0 if r[3] is not None else 0.0,
                "group_column": "ai_gateway",
            },
        )
    return out


def build_runtime_flow_from_gateway(days: int = 7) -> dict[str, Any]:
    """Runtime flow when inference payload tables are unavailable."""
    hours_gw = max(1, min(90 * 24, int(days) * 24))
    gw = ai_gateway_usage_rollup(hours_gw)
    out: dict[str, Any] = {
        "window_days": int(days),
        "distinct_callers": None,
        "total_requests": int(gw.get("total_requests") or 0) if not gw.get("error") else None,
        "routes": [],
        "models": [],
        "callers": [],
        "error": gw.get("error"),
        "note": "From system.ai_gateway.usage — inference payload tables unavailable.",
    }
    if gw.get("error"):
        return out
    for r in gw.get("by_model") or []:
        m = str(r.get("model") or "unknown")
        c = int(r.get("requests") or 0)
        out["models"].append({"model": m, "requests": c})
        out["routes"].append(
            {
                "destination_id": m,
                "url": None,
                "api_type": "ai_gateway.usage",
                "requests": c,
            },
        )
    return out


def quality_score_from_observability(qo: dict[str, Any]) -> float | None:
    """0–1 composite: low errors, low p95 latency, optional reasoning presence."""
    if qo.get("error"):
        return None
    if qo.get("p95_latency_ms") is None and qo.get("error_rate_pct") is None:
        return None
    er = min(1.0, max(0.0, float(qo.get("error_rate_pct") or 0) / 100.0))
    p95 = float(qo.get("p95_latency_ms") or 0)
    lat_n = min(1.0, max(0.0, p95 / 25_000.0))
    rs = min(1.0, max(0.0, float(qo.get("responses_with_reasoning_pct") or 0) / 100.0))
    return max(0.0, min(1.0, (1.0 - er) * 0.55 + (1.0 - lat_n) * 0.25 + rs * 0.2))


def quality_observability_from_gateway(hours: int = 24) -> dict[str, Any]:
    where = _gateway_usage_where_clause(hours)
    out: dict[str, Any] = {
        "window_hours": int(hours),
        "avg_latency_ms": None,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "error_rate_pct": None,
        "requests_sampled_for_json": 0,
        "responses_with_reasoning_pct": None,
        "error": None,
        "source": "ai_gateway",
    }
    sql = (
        "SELECT AVG(latency_ms), approx_percentile(latency_ms, 0.5), "
        "approx_percentile(latency_ms, 0.95), "
        "AVG(CASE WHEN CAST(status_code AS DOUBLE) >= 400 "
        "OR CAST(status_code AS DOUBLE) < 100 THEN 1.0 ELSE 0.0 END) "
        f"FROM system.ai_gateway.usage {where}"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
        if row:
            out["avg_latency_ms"] = float(row[0]) if row[0] is not None else None
            out["p50_latency_ms"] = float(row[1]) if row[1] is not None else None
            out["p95_latency_ms"] = float(row[2]) if row[2] is not None else None
            out["error_rate_pct"] = float(row[3]) * 100.0 if row[3] is not None else 0.0
        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
    return out


def ai_gateway_usage_rollup(
    hours: int,
    gateway_model: str | None = None,
    gateway_models: list[str] | None = None,
    *,
    request_id: str | None = None,
    request_ids: list[str] | None = None,
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
    rid_eff = _normalize_request_ids(request_id=request_id, request_ids=request_ids)
    rid_clause = ""
    if len(rid_eff) == 1:
        esc = rid_eff[0].replace("'", "''")
        rid_clause = f" AND request_id = '{esc}' "
    elif len(rid_eff) > 1:
        in_list = ",".join("'" + r.replace("'", "''") + "'" for r in rid_eff)
        rid_clause = f" AND request_id IN ({in_list}) "
    no_row_clause = " AND 1=0 " if gateway_no_rows else ""
    test_excl_gw = ""
    if not rid_eff:
        from app.services.compare_run import gateway_exclude_test_request_ids_clause

        test_excl_gw = gateway_exclude_test_request_ids_clause(h)
    # Pinned request(s): match by ID across all time — not only the rolling hours window.
    time_clause = (
        ""
        if rid_eff
        else f" AND event_time >= current_timestamp() - INTERVAL {h} HOURS "
    )
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
        "gateway_request_id_filter": rid_eff[0] if len(rid_eff) == 1 else None,
        "gateway_request_ids_filter": rid_eff if len(rid_eff) > 1 else None,
        "gateway_no_rows": bool(gateway_no_rows),
        "gateway_time_filter_skipped": bool(rid_eff),
    }
    where_body = f"WHERE 1=1{time_clause}{w}{gm_clause}{rid_clause}{test_excl_gw}{no_row_clause}"
    sql_tot = (
        "SELECT COUNT(*), "
        "COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(total_tokens), 0) "
        f"FROM system.ai_gateway.usage {where_body}"
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
        f"FROM system.ai_gateway.usage {where_body}"
        " GROUP BY 1 ORDER BY ttot DESC NULLS LAST LIMIT 25"
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
    if not out.get("error"):
        from app.services.gateway_aliases import apply_aliases_to_gateway_rollup

        return apply_aliases_to_gateway_rollup(out)
    return out


def _normalize_request_ids(
    request_id: str | None = None,
    request_ids: list[str] | None = None,
    *,
    max_ids: int = 32,
) -> list[str]:
    """Sanitized unique request_id values for SQL IN / gateway filters."""
    out: list[str] = []
    seen: set[str] = set()
    raw: list[str] = []
    if request_ids:
        raw.extend(str(x) for x in request_ids)
    if request_id and str(request_id).strip():
        raw.append(str(request_id).strip())
    for r in raw:
        rid = str(r or "").strip()
        if not rid or not re.match(r"^[a-zA-Z0-9_\-\.]+$", rid):
            continue
        if rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
        if len(out) >= max_ids:
            break
    return out


def _inference_request_id_predicate(cmap: dict[str, str], request_id: str | None) -> str:
    ids = _normalize_request_ids(request_id=request_id)
    return _inference_request_ids_predicate(cmap, ids)


def _inference_request_ids_predicate(cmap: dict[str, str], request_ids: list[str]) -> str:
    if not request_ids or "request_id" not in cmap:
        return ""
    if len(request_ids) == 1:
        esc = request_ids[0].replace("'", "''")
        return f" AND CAST(`{cmap['request_id']}` AS STRING) = '{esc}' "
    in_list = ",".join("'" + r.replace("'", "''") + "'" for r in request_ids)
    return f" AND CAST(`{cmap['request_id']}` AS STRING) IN ({in_list}) "


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
    parsed_req: Any = None,
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

    requester_raw = str(record.get("requester") or "").strip()
    if not requester_raw and gateway:
        requester_raw = str(gateway.get("requester") or "").strip()
    oauth_label = (_caller_display_from_record(record, requester_raw) or "").strip()
    payload_actor = (_caller_hint_from_chat_request_json(parsed_req) or "").strip()
    if payload_actor:
        if oauth_label and payload_actor.lower() != oauth_label.lower():
            caller_label = f"{payload_actor}\n↳ OAuth / Apps principal: {oauth_label}"
        else:
            caller_label = payload_actor
    else:
        caller_label = oauth_label or ""
    push("Caller", caller_label or "Authenticated client", "caller")

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

    tin_i = tout_i = tot_i = None
    metering_src_eff = ""
    if gateway:
        tt_raw = gateway.get("total_tokens")
        if tt_raw is not None:
            try:
                tot_i = int(float(tt_raw))
                tin_i = int(float(gateway.get("input_tokens") or 0))
                tout_i = int(float(gateway.get("output_tokens") or 0))
                metering_src_eff = str(gateway.get("metering_source") or "ai_gateway_exact_request_id")
            except (TypeError, ValueError):
                tin_i = tout_i = tot_i = None
                metering_src_eff = ""

    if tot_i is None or tot_i <= 0:
        ud = _usage_tokens_deep(parsed_resp)
        if ud and ud.get("total_tokens") is not None:
            try:
                tot_i = int(ud["total_tokens"])
                if ud.get("input_tokens") is not None:
                    tin_i = int(float(ud["input_tokens"]))
                elif tin_i is None:
                    tin_i = 0
                if ud.get("output_tokens") is not None:
                    tout_i = int(float(ud["output_tokens"]))
                elif tout_i is None:
                    tout_i = 0
                metering_src_eff = "completion_payload_lineage_fallback"
            except (TypeError, ValueError):
                tot_i = tin_i = tout_i = None

    tok_block = "Token counts not in logged payload — enable completion.usage in responses or redeploy AgentOps metering fixes."
    if tot_i is not None:
        tin_i = int(tin_i or 0)
        tout_i = int(tout_i or 0)
        if metering_src_eff == "completion_response_json":
            tok_block = (
                f"input {tin_i:,} · output {tout_i:,} · total {tot_i:,} "
                f"(from completion.usage in payload — Gateway row not keyed by inference request_id)"
            )
        elif metering_src_eff == "completion_payload_lineage_fallback":
            tok_block = (
                f"input {tin_i:,} · output {tout_i:,} · total {tot_i:,} "
                f"(from nested completion.usage inside logged response)"
            )
        elif metering_src_eff == "ai_gateway_heuristic_time_destination":
            tok_block = (
                f"input {tin_i:,} · output {tout_i:,} · total {tot_i:,} "
                f"(AI Gateway metering — matched by time + destination)"
            )
        elif metering_src_eff.startswith("ai_gateway"):
            tok_block = f"input {tin_i:,} · output {tout_i:,} · total {tot_i:,} (AI Gateway metering)"
        else:
            tok_block = f"input {tin_i:,} · output {tout_i:,} · total {tot_i:,}"
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

    fqn = str(record.get("_agentops_source_table") or record.get("source_table") or "").strip()
    if fqn:
        push("Payload log table", f"Unity Catalog: {fqn}\nAgentOps reads request/response JSON here.", "storage")
    elif gateway and gateway.get("request_id"):
        push("Payload log table", "See Agents → request list (inference payload tables).", "storage")

    bill_lines: list[str] = []
    if tot_i is not None:
        if metering_src_eff in ("completion_response_json", "completion_payload_lineage_fallback"):
            bill_lines.append("Token counts inferred from logged completion JSON (usage block)")
        elif metering_src_eff == "ai_gateway_heuristic_time_destination":
            bill_lines.append("AI Gateway metering correlated by timestamp + destination")
        else:
            bill_lines.append("Metered via system.ai_gateway.usage when available")
    bill_lines.append("List price: open Cost tab (billing.usage × list_prices)")
    rid_bill = str(record.get("request_id") or "").strip()
    if not rid_bill and gateway:
        rid_bill = str(gateway.get("request_id") or "").strip()
    if rid_bill:
        bill_lines.append(f"request_id: {rid_bill}")
    push("Billing & cost", "\n".join(bill_lines), "billing")

    for i in range(len(nodes) - 1):
        edges.append({"from": nodes[i]["id"], "to": nodes[i + 1]["id"]})
    return {"nodes": nodes, "edges": edges}


def _ctx_test_excl(ctx: dict[str, Any] | None) -> str:
    if not ctx:
        return ""
    return str(ctx.get("test_excl") or "")


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
    from app.services.compare_run import test_request_sql_exclude_fragment

    ctx = {
        "fqn": fqns[0],
        "fqns": fqns,
        "fqn_label": fqn_label,
        "table_sql": table_sql,
        "time_col": time_col,
        "cols": cols,
        "test_excl": test_request_sql_exclude_fragment(cols),
        "status_col": pick_col(avail, STATUS_CANDIDATES),
        "latency_col": pick_col(avail, LATENCY_CANDIDATES),
        "request_body_col": pick_col(avail, REQUEST_BODY_CANDIDATES),
        "response_body_col": pick_col(avail, RESPONSE_BODY_CANDIDATES),
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
    tx = _ctx_test_excl(ctx)
    cmap = {c.lower(): c for c in ctx["cols"]}
    window = max(1, min(90, int(days)))
    out: dict[str, Any] = {
        "window_days": int(days),
        "distinct_callers": None,
        "total_requests": None,
        "routes": [],
        "models": [],
        "callers": [],
        "error": None,
    }
    try:
        has_req = "requester" in cmap
        has_resp = "response" in cmap
        if has_req:
            sql0 = (
                f"SELECT COUNT(DISTINCT `{cmap['requester']}`), COUNT(*) FROM {tbl} "
                f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS{tx}"
            )
        else:
            sql0 = (
                f"SELECT CAST(NULL AS BIGINT), COUNT(*) FROM {tbl} "
                f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS{tx}"
            )
        dest_e = f"`{cmap['destination_id']}`" if "destination_id" in cmap else "CAST(NULL AS STRING)"
        url_e = f"`{cmap['url']}`" if "url" in cmap else "CAST(NULL AS STRING)"
        api_e = f"`{cmap['api_type']}`" if "api_type" in cmap else "CAST(NULL AS STRING)"
        sql_r = (
            f"SELECT CAST({dest_e} AS STRING), CAST({url_e} AS STRING), CAST({api_e} AS STRING), "
            f"COUNT(*) AS c FROM {tbl} "
            f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS{tx} "
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
            if has_req:
                req_c = cmap["requester"]
                sql_callers = (
                    f"SELECT CAST(`{req_c}` AS STRING) AS requester, COUNT(*) AS c, "
                    f"MAX(`{tc}`) AS last_seen FROM {tbl} "
                    f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS{tx} "
                    f"AND `{req_c}` IS NOT NULL AND TRIM(CAST(`{req_c}` AS STRING)) != '' "
                    f"GROUP BY 1 ORDER BY c DESC NULLS LAST LIMIT 30"
                )
                cur.execute(sql_callers)
                for crow in cur.fetchall() or []:
                    if not crow[0]:
                        continue
                    ls = crow[2]
                    out["callers"].append(
                        {
                            "requester": str(crow[0]),
                            "requests": int(crow[1] or 0),
                            "last_seen": ls.isoformat()
                            if ls is not None and hasattr(ls, "isoformat")
                            else str(ls) if ls is not None else None,
                        },
                    )
            if has_resp:
                sql_m = (
                    f"SELECT get_json_object(CAST(`{cmap['response']}` AS STRING), '$.model') AS m, "
                    f"COUNT(*) AS c FROM {tbl} "
                    f"WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS{tx} "
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
    gateway_models: list[str] | None = None,
) -> dict[str, Any]:
    """Hourly request + error counts."""
    ctx, err = _inference_table_ctx()
    h = int(hours)
    out: dict[str, Any] = {
        "hours": h,
        "buckets": [],
        "error": err,
        "source": "inference_table",
    }
    gm = list(gateway_models) if gateway_models else None
    if err or not ctx:
        where = _gateway_usage_where_clause(h, gm)
        sql_gw = (
            "SELECT date_trunc('HOUR', event_time) AS bucket, "
            "COUNT(*) AS requests, "
            "SUM(CASE WHEN CAST(status_code AS DOUBLE) >= 400 "
            "OR CAST(status_code AS DOUBLE) < 100 THEN 1 ELSE 0 END) AS errors "
            f"FROM system.ai_gateway.usage {where} GROUP BY 1 ORDER BY 1 ASC"
        )
        try:
            with sql_connection() as conn:
                cur = conn.cursor()
                cur.execute(sql_gw)
                rows = cur.fetchall() or []
            buckets: list[dict[str, Any]] = []
            for r in rows:
                if not r or r[0] is None:
                    continue
                b = r[0]
                bt = b.isoformat() if hasattr(b, "isoformat") else str(b)
                buckets.append(
                    {
                        "bucket": bt,
                        "requests": int(r[1] or 0),
                        "errors": int(r[2] or 0),
                    },
                )
            if buckets:
                out["buckets"] = buckets
                out["error"] = None
                out["source"] = "ai_gateway"
                out["note"] = "Hourly buckets from AI Gateway (payload inference tables unavailable)."
                return out
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e).strip()[:500]
            return out
        return out
    tc = ctx["time_col"]
    sc = ctx["status_col"]
    tbl = ctx["table_sql"]
    if source_tables:
        extra = _ctx_source_predicate_multi(ctx, source_tables)
    else:
        extra = _ctx_source_predicate(ctx, source_table)
    extra = extra + _ctx_test_excl(ctx)
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
    gateway_models: list[str] | None = None,
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
        "source": "inference_table",
    }
    gm = list(gateway_models) if gateway_models else None
    if err or not ctx:
        where = _gateway_usage_where_clause(24, gm)
        sql_gw = (
            "SELECT approx_percentile(latency_ms, 0.95) AS p95, "
            "AVG(CASE WHEN CAST(status_code AS DOUBLE) >= 400 "
            "OR CAST(status_code AS DOUBLE) < 100 THEN 1.0 ELSE 0.0 END) AS er "
            f"FROM system.ai_gateway.usage {where}"
        )
        try:
            with sql_connection() as conn:
                cur = conn.cursor()
                cur.execute(sql_gw)
                row = cur.fetchone()
            if row:
                out["global_p95_ms"] = float(row[0]) if row[0] is not None else None
                out["global_error_rate_pct"] = (
                    float(row[1]) * 100.0 if row[1] is not None else None
                )
            rollups = gateway_agent_rollups(limit=50, hours=24, gateway_models=gm)
            out["rollups"] = rollups
            out["agents_breaching_p95"] = sum(
                1 for r in rollups if float(r.get("p95_latency_ms") or 0) > p95_target_ms
            )
            out["agents_over_error_budget"] = sum(
                1 for r in rollups if float(r.get("error_rate_pct") or 0) > error_budget_pct
            )
            out["error"] = None
            out["source"] = "ai_gateway"
            out["note"] = (
                "SLO from AI Gateway (24h). Payload inference tables unavailable — "
                "fix Unity Catalog storage credential for full log drill-down."
            )
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e).strip()[:500]
        return out
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    lc = ctx["latency_col"]
    sc = ctx["status_col"]
    if source_tables:
        extra = _ctx_source_predicate_multi(ctx, source_tables)
    else:
        extra = _ctx_source_predicate(ctx, source_table)
    extra = extra + _ctx_test_excl(ctx)
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


def _cost_by_request_breakdown(
    ctx: dict[str, Any],
    request_ids: list[str],
    hours: int,
    pred_base: str,
    tok_sql: str,
    cmap: dict[str, str],
    *,
    scoped_list_usd: float | None,
    row_cache: dict[str, tuple[dict[str, Any] | None, Any, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Per-request gateway tokens + inference proxy + prorated list USD."""
    if not request_ids:
        return []
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    rq_col = cmap.get("request_id")
    inf_by_rid: dict[str, tuple[float, int]] = {}
    if rq_col and tok_sql != "CAST(0.0 AS DOUBLE)":
        in_list = ",".join("'" + r.replace("'", "''") + "'" for r in request_ids)
        sql = (
            f"SELECT CAST(`{rq_col}` AS STRING) AS rid, COUNT(*) AS c, SUM({tok_sql}) AS est "
            f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {int(hours)} HOURS "
            f"{pred_base} AND CAST(`{rq_col}` AS STRING) IN ({in_list}) "
            f"GROUP BY 1"
        )
        try:
            with sql_connection() as conn:
                cur = conn.cursor()
                cur.execute(sql)
                for row in cur.fetchall() or []:
                    if row and row[0]:
                        inf_by_rid[str(row[0])] = (float(row[2] or 0), int(row[1] or 0))
        except Exception:  # noqa: BLE001
            pass

    gw_map, _ = _fetch_ai_gateway_usage_batch(request_ids, limit=len(request_ids))
    merged: dict[str, dict[str, Any]] = {}
    for rid in request_ids:
        row = dict(gw_map.get(rid) or {})
        if not row.get("total_tokens"):
            if row_cache is not None:
                if rid not in row_cache:
                    row_cache[rid] = _fetch_inference_record_and_parsed(ctx, rid)
                rec, pq, ps = row_cache[rid]
            else:
                rec, pq, ps = _fetch_inference_record_and_parsed(ctx, rid)
            gwm, _ = resolve_ai_gateway_metering(ctx, rid, rec, ps, pq)
            if gwm:
                row = {**row, **gwm}
        merged[rid] = row

    sum_gw_tok = 0
    for rid in request_ids:
        u = merged[rid]
        if u.get("total_tokens") is not None:
            try:
                sum_gw_tok += int(u["total_tokens"])
            except (TypeError, ValueError):
                pass

    rows_out: list[dict[str, Any]] = []
    for rid in request_ids:
        u = merged[rid]
        est_inf, req_c = inf_by_rid.get(rid, (0.0, 0))
        tt = u.get("total_tokens")
        try:
            tt_i = int(tt) if tt is not None else None
        except (TypeError, ValueError):
            tt_i = None
        est_usd = None
        if scoped_list_usd is not None and sum_gw_tok > 0 and tt_i is not None:
            est_usd = round(float(scoped_list_usd) * (tt_i / float(sum_gw_tok)), 6)
        rows_out.append(
            {
                "request_id": rid,
                "gateway_input_tokens": u.get("input_tokens"),
                "gateway_output_tokens": u.get("output_tokens"),
                "gateway_total_tokens": tt_i,
                "est_payload_tokens": round(est_inf, 1),
                "inference_requests": req_c,
                "est_list_usd_prorated": est_usd,
                "destination_model": u.get("destination_model") or u.get("destination_name"),
            }
        )
    return rows_out


def _sync_gateway_totals_from_by_request(
    ag: dict[str, Any],
    by_request: list[dict[str, Any]],
) -> dict[str, Any]:
    """If rollup missed tokens but per-request rows have gateway data, fill summary cards."""
    if ag.get("error") or not by_request:
        return ag
    if int(ag.get("total_tokens") or 0) > 0:
        return ag
    sum_tok = 0
    sum_in = 0
    sum_out = 0
    n = 0
    for row in by_request:
        tt = row.get("gateway_total_tokens")
        if tt is None:
            continue
        try:
            tti = int(tt)
        except (TypeError, ValueError):
            continue
        if tti <= 0:
            continue
        sum_tok += tti
        n += 1
        try:
            sum_in += int(row.get("gateway_input_tokens") or 0)
            sum_out += int(row.get("gateway_output_tokens") or 0)
        except (TypeError, ValueError):
            pass
    if sum_tok <= 0:
        return ag
    merged = dict(ag)
    merged["total_tokens"] = sum_tok
    merged["total_input_tokens"] = sum_in
    merged["total_output_tokens"] = sum_out
    merged["total_requests"] = n
    note = (merged.get("note") or "").strip()
    extra = "Summary totals from per-request gateway rows."
    merged["note"] = f"{note} {extra}".strip() if note else extra
    return merged


def cost_summary(
    hours: int = 168,
    source_table: str | None = None,
    gateway_model: str | None = None,
    *,
    source_tables: list[str] | None = None,
    gateway_models: list[str] | None = None,
    request_id: str | None = None,
    request_ids: list[str] | None = None,
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

    rid_list = _normalize_request_ids(request_id=request_id, request_ids=request_ids)
    rid = rid_list[0] if len(rid_list) == 1 else None
    ctx, err = _inference_table_ctx()
    derived_gateway_destination_ids: list[str] | None = None
    row_cache: dict[str, tuple[dict[str, Any] | None, Any, Any]] = {}

    if rid_list:
        ag = ai_gateway_usage_rollup(
            hours,
            request_id=rid if len(rid_list) == 1 else None,
            request_ids=rid_list if len(rid_list) > 1 else None,
        )
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

    if rid_list and ctx and not err:
        ag = _enrich_gateway_rollup_for_pinned_requests(ag, ctx, rid_list, row_cache)

    has_focus = bool(rid_list or st_eff or gw_eff or (source_table and str(source_table).strip()))
    billing_terms_param: list[str] | None = None
    billing_rid_exact: list[str] | None = None
    if has_focus:
        if rid_list:
            billing_rid_exact = rid_list
        elif st_eff:
            billing_terms_param = _billing_terms_from_inference_fqns(st_eff)
        else:
            bm = (ag or {}).get("by_model") or []
            labs = [str(x.get("model") or "") for x in bm if x.get("model")]
            if labs:
                billing_terms_param = _collect_billing_endpoint_match_terms(*labs)
            else:
                billing_terms_param = []

    gw_tokens_for_bill = int((ag or {}).get("total_tokens") or 0)
    bill = billing_model_serving_cost(
        hours,
        endpoint_match_terms=billing_terms_param if has_focus and not billing_rid_exact else None,
        endpoint_exact_request_ids=billing_rid_exact,
        gateway_total_tokens=gw_tokens_for_bill,
    )
    if (
        has_focus
        and not rid_list
        and bill
        and not bill.get("error")
        and (float(bill.get("total_list_usd") or 0) == 0 and float(bill.get("total_dbu") or 0) == 0)
    ):
        bill_ws = billing_model_serving_cost(
            hours,
            endpoint_match_terms=None,
            gateway_total_tokens=gw_tokens_for_bill,
        )
        wu = float(bill_ws.get("total_list_usd") or 0) if bill_ws and not bill_ws.get("error") else 0.0
        wd = float(bill_ws.get("total_dbu") or 0) if bill_ws and not bill_ws.get("error") else 0.0
        if bill_ws and not bill_ws.get("error") and (wu > 0 or wd > 0):
            by_ws = bill_ws.get("by_endpoint") or []
            bill["workspace_reference"] = {
                "hours": bill_ws.get("hours"),
                "total_dbu": bill_ws.get("total_dbu"),
                "total_list_usd": bill_ws.get("total_list_usd"),
                "currency_code": bill_ws.get("currency_code"),
                "by_endpoint": by_ws[:15] if isinstance(by_ws, list) else [],
                "pricing_partial": bill_ws.get("pricing_partial"),
                "note": (
                    "Workspace-wide MODEL_SERVING list price for the same window — scoped filter matched no "
                    "billing.usage rows (gateway / log labels often differ from endpoint_name in billing)."
                ),
            }
            bill["note"] = (
                (bill.get("note") or "").strip()
                + " workspace_reference holds unscoped MODEL_SERVING totals for comparison."
            ).strip()

    if (
        billing_rid_exact
        and bill
        and not bill.get("error")
        and float(bill.get("total_dbu") or 0) == 0
        and gw_tokens_for_bill > 0
    ):
        pin_terms: list[str] = []
        for prid in billing_rid_exact:
            pin_terms.extend(_resolve_billing_terms_for_pinned_request(ctx, prid, row_cache=row_cache))
        pin_terms = list(dict.fromkeys(t for t in pin_terms if t))
        if pin_terms:
            bill_terms = billing_model_serving_cost(
                hours,
                endpoint_match_terms=pin_terms,
                gateway_total_tokens=gw_tokens_for_bill,
            )
            if bill_terms and not bill_terms.get("error"):
                td = float(bill_terms.get("total_dbu") or 0)
                tu = float(bill_terms.get("total_list_usd") or 0)
                if td > 0 or tu > 0:
                    bill = bill_terms
                    bill["attribution"] = "request_pinned"

        if float(bill.get("total_dbu") or 0) == 0:
            ag_ws = ai_gateway_usage_rollup(hours)
            ws_tok = int(ag_ws.get("total_tokens") or 0) if not ag_ws.get("error") else 0
            bill_ws = billing_model_serving_cost(
                hours,
                endpoint_match_terms=None,
                gateway_total_tokens=ws_tok or None,
            )
            wu = float(bill_ws.get("total_list_usd") or 0) if bill_ws and not bill_ws.get("error") else 0.0
            wd = float(bill_ws.get("total_dbu") or 0) if bill_ws and not bill_ws.get("error") else 0.0
            if ws_tok > 0 and gw_tokens_for_bill > 0 and (wd > 0 or wu > 0):
                ratio = min(1.0, gw_tokens_for_bill / ws_tok)
                bill["total_dbu"] = round(wd * ratio, 6)
                bill["total_list_usd"] = round(wu * ratio, 6)
                bill["attribution"] = "request_pinned_token_prorated"
                bill["note"] = (
                    (bill.get("note") or "").strip()
                    + " DBU/list USD prorated from workspace billing.usage by gateway token share "
                    f"({gw_tokens_for_bill:,} pinned / {ws_tok:,} workspace). "
                    "Databricks billing has no per-request_id column."
                ).strip()
                by_ws = bill_ws.get("by_endpoint") or []
                if isinstance(by_ws, list) and by_ws:
                    scaled: list[dict[str, Any]] = []
                    for ep in by_ws[:15]:
                        if not isinstance(ep, dict):
                            continue
                        scaled.append(
                            {
                                **ep,
                                "dbu": round(float(ep.get("dbu") or 0) * ratio, 6),
                                "list_usd": (
                                    round(float(ep.get("list_usd") or 0) * ratio, 6)
                                    if ep.get("list_usd") is not None
                                    else None
                                ),
                            },
                        )
                    bill["by_endpoint"] = scaled
                bill["workspace_reference"] = {
                    "hours": bill_ws.get("hours"),
                    "total_dbu": bill_ws.get("total_dbu"),
                    "total_list_usd": bill_ws.get("total_list_usd"),
                    "currency_code": bill_ws.get("currency_code"),
                    "prorate_ratio": round(ratio, 6),
                    "note": "Full workspace MODEL_SERVING totals before proration to pinned requests.",
                }

    out: dict[str, Any] = {
        "hours": int(hours),
        "method": "char_length_over_4_proxy",
        "note": "",
        "total_est_tokens": None,
        "by_destination": [],
        "hourly": [],
        "by_request": [],
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
            "request_ids": rid_list if len(rid_list) > 1 else None,
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
        _attach_token_cost_estimate(out)
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
    pred = pred_base + _ctx_test_excl(ctx) + _inference_request_ids_predicate(cmap, rid_list)
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
    if rid_list and ctx and not err:
        scoped_usd = bill.get("total_list_usd") if bill and not bill.get("error") else None
        out["by_request"] = _cost_by_request_breakdown(
            ctx,
            rid_list,
            hours,
            pred_base,
            tok,
            cmap,
            scoped_list_usd=float(scoped_usd) if scoped_usd is not None else None,
            row_cache=row_cache,
        )
        out["ai_gateway"] = _sync_gateway_totals_from_by_request(ag, out["by_request"])
        ag = out["ai_gateway"]
    if rid_list and not (err and not ctx):
        out["note"] = (
            (out.get("note") or "")
            + " Pinned task: gateway tokens prioritize system.ai_gateway.usage; when inference request_id differs "
            "(typical for Databricks Apps), AgentOps resolves alternate ids, near-time matching, then completion.usage."
        ).strip()
    if not ag.get("error") and (ag.get("total_tokens") or 0) > 0:
        out["token_primary_source"] = "ai_gateway"
    elif out.get("total_est_tokens"):
        out["token_primary_source"] = "payload_proxy"
    _attach_token_cost_estimate(out)
    return out


def _attach_token_cost_estimate(out: dict[str, Any]) -> None:
    try:
        from app.services.compare_run import cost_estimate_meta

        est_meta = cost_estimate_meta()
        per_1m = est_meta.get("usd_per_1m_tokens")
        tt = int((out.get("ai_gateway") or {}).get("total_tokens") or 0)
        est_usd = (
            round(tt * float(per_1m) / 1_000_000.0, 6) if per_1m is not None and tt > 0 else None
        )
        out["token_cost_estimate"] = {
            "usd_per_1m_tokens": per_1m,
            "estimated_usd": est_usd,
            "note": est_meta.get("note"),
        }
    except Exception:  # noqa: BLE001
        out["token_cost_estimate"] = None


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
    req_sql_col = rq
    if not req_sql_col:
        for cand in REQUEST_BODY_CANDIDATES:
            hc = cmap.get(str(cand).lower())
            if hc:
                req_sql_col = hc
                break
    if rq:
        parts.append(f"SUBSTRING(CAST(`{rq}` AS STRING), 1, 320) AS request_preview")
    else:
        parts.append("CAST(NULL AS STRING) AS request_preview")
    if rs:
        parts.append(f"SUBSTRING(CAST(`{rs}` AS STRING), 1, 320) AS response_preview")
    else:
        parts.append("CAST(NULL AS STRING) AS response_preview")
    if req_sql_col:
        parts.append(
            "COALESCE("
            f"NULLIF(TRIM(GET_JSON_OBJECT(CAST(`{req_sql_col}` AS STRING), '$.user')), ''), "
            f"NULLIF(TRIM(GET_JSON_OBJECT(CAST(`{req_sql_col}` AS STRING), '$.metadata.display_name')), ''), "
            f"NULLIF(TRIM(GET_JSON_OBJECT(CAST(`{req_sql_col}` AS STRING), '$.metadata.email')), '')"
            ") AS request_actor"
        )
    else:
        parts.append("CAST(NULL AS STRING) AS request_actor")

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

    test_excl = ctx.get("test_excl") or ""
    sql = (
        f"SELECT {', '.join(parts)} FROM {tbl} "
        f"WHERE 1=1 {pred_st} {gm_extra}{test_excl} ORDER BY `{tc}` DESC NULLS LAST LIMIT {lim}"
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
                    "request_actor": (
                        str(rec["request_actor"]).strip() if rec.get("request_actor") is not None else None
                    ),
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


def _parse_trace_payloads(req_raw: Any, resp_raw: Any) -> tuple[Any, Any, str | None, str | None]:
    """Parse inference request/response bodies. Do not truncate dict/VARIANT before json.loads."""
    parsed_req: Any = None
    parsed_resp: Any = None
    disp_req: str | None = None
    disp_resp: str | None = None

    if isinstance(req_raw, (dict, list)):
        parsed_req = req_raw
        disp_req = json.dumps(req_raw, default=str, ensure_ascii=False)
    elif req_raw is not None:
        s = str(req_raw)
        disp_req = s
        try:
            parsed_req = json.loads(s)
        except json.JSONDecodeError:
            parsed_req = None

    if isinstance(resp_raw, (dict, list)):
        parsed_resp = resp_raw
        disp_resp = json.dumps(resp_raw, default=str, ensure_ascii=False)
    elif resp_raw is not None:
        s = str(resp_raw)
        disp_resp = s
        try:
            parsed_resp = json.loads(s)
        except json.JSONDecodeError:
            parsed_resp = None

    def cap(s: str | None, lim: int) -> str | None:
        if s is None:
            return None
        return s if len(s) <= lim else s[: max(0, lim - 24)] + "\n…(truncated for API)"

    return parsed_req, parsed_resp, cap(disp_req, 100_000), cap(disp_resp, 200_000)


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
        avail_row = set(col_names)
        req_pick = ctx.get("request_body_col") or pick_col(avail_row, REQUEST_BODY_CANDIDATES)
        rsp_pick = ctx.get("response_body_col") or pick_col(avail_row, RESPONSE_BODY_CANDIDATES)
        req_raw = record.get(req_pick) if req_pick else None
        resp_raw = record.get(rsp_pick) if rsp_pick else None
        if req_raw is None:
            for cand in REQUEST_BODY_CANDIDATES:
                hit = next((c for c in col_names if c.lower() == cand.lower()), None)
                if hit:
                    req_raw = record.get(hit)
                    break
        if resp_raw is None:
            for cand in RESPONSE_BODY_CANDIDATES:
                hit = next((c for c in col_names if c.lower() == cand.lower()), None)
                if hit:
                    resp_raw = record.get(hit)
                    break

        parsed_req, parsed_resp, disp_req, disp_resp = _parse_trace_payloads(req_raw, resp_raw)

        reasoning = _extract_reasoning_summary(disp_resp) if disp_resp else None
        gw, gw_err = resolve_ai_gateway_metering(ctx, rid, record, parsed_resp, parsed_req)
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
            src = str(gw.get("metering_source") or "")
            detail = (
                f"in={gw.get('input_tokens')}, out={gw.get('output_tokens')}, "
                f"total={gw.get('total_tokens')}"
            )
            if src == "completion_response_json":
                step_title = "Token usage (response JSON)"
                detail += " — from completion.usage; inference request_id did not join system.ai_gateway.usage."
            elif src == "ai_gateway_heuristic_time_destination":
                step_title = "Token usage (AI Gateway, heuristic)"
                detail += " — matched by event time + destination_id (alternate request ids)."
            else:
                step_title = "Token usage (AI Gateway)"
            internal_lineage.append({"step": step_title, "detail": detail})
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
        lineage_graph = _build_request_lineage_graph(serializable, parsed_resp, gw, reasoning, parsed_req)
        gw_tok = int(gw.get("total_tokens") or 0) if gw else 0
        bill = billing_model_serving_cost(
            24 * 7,
            endpoint_exact_request_ids=[rid],
            gateway_total_tokens=gw_tok or None,
        )
        cost_attribution = {
            "request_id": rid,
            "gateway_tokens": gw_tok if gw else None,
            "metering_source": (gw.get("metering_source") if gw else None),
            "list_usd": bill.get("total_list_usd"),
            "dbu": bill.get("total_dbu"),
            "attribution": bill.get("attribution"),
            "by_endpoint": (bill.get("by_endpoint") or [])[:8],
            "note": bill.get("note"),
            "error": bill.get("error"),
        }

        response_for_ui: Any = None
        if parsed_resp is not None:
            response_for_ui = parsed_resp
        elif disp_resp:
            response_for_ui = {
                "_note": "Could not parse as JSON — showing stored body (truncated may apply).",
                "_raw": disp_resp,
            }

        req_for_ui = parsed_req if parsed_req is not None else disp_req

        return {
            "request_id": rid,
            "comparison_group_id": comparison_group_id,
            "record": serializable,
            "payload_columns_resolved": {"request_body": req_pick, "response_body": rsp_pick},
            "request_json": req_for_ui,
            "response_json": response_for_ui,
            "response_raw": None,
            "reasoning_summary": reasoning,
            "internal_lineage": internal_lineage,
            "lineage_graph": lineage_graph,
            "ai_gateway_usage": gw,
            "ai_gateway_usage_error": gw_err,
            "cost_attribution": cost_attribution,
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e).strip()[:500], "request_id": rid}


def _inference_token_sql_from_cmap(cmap: dict[str, str]) -> str:
    """SQL expression for payload token estimate (log columns or char/4 proxy)."""
    if "request" in cmap and "response" in cmap:
        rqc, rsc = cmap["request"], cmap["response"]
        return f"(LENGTH(CAST(`{rqc}` AS STRING)) + LENGTH(CAST(`{rsc}` AS STRING))) / 4.0"
    if "total_tokens" in cmap:
        return f"COALESCE(CAST(`{cmap['total_tokens']}` AS DOUBLE), 0.0)"
    if "input_tokens" in cmap and "output_tokens" in cmap:
        return (
            f"(COALESCE(CAST(`{cmap['input_tokens']}` AS DOUBLE), 0.0) + "
            f"COALESCE(CAST(`{cmap['output_tokens']}` AS DOUBLE), 0.0))"
        )
    return "CAST(0.0 AS DOUBLE)"


def _per_table_est_tokens(ctx: dict[str, Any], days: int = 7) -> dict[str, int]:
    """Estimated tokens (char/4 proxy) per payload table."""
    fqns = list(ctx.get("fqns") or [])
    if len(fqns) <= 1:
        return {}
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    cmap = {c.lower(): c for c in ctx["cols"]}
    tok_sql = _inference_token_sql_from_cmap(cmap)
    if tok_sql == "CAST(0.0 AS DOUBLE)":
        return {}
    window = max(1, min(90, int(days)))
    tx = _ctx_test_excl(ctx)
    out: dict[str, int] = {}
    try:
        sql = (
            f"SELECT LOWER(TRIM(CAST(`_agentops_source_table` AS STRING))), "
            f"CAST(SUM({tok_sql}) AS BIGINT) "
            f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS{tx} "
            "GROUP BY 1"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            for row in cur.fetchall() or []:
                if row[0]:
                    out[str(row[0]).lower()] = int(row[1] or 0)
    except Exception:
        return {}
    return out


def _per_table_request_counts(ctx: dict[str, Any], days: int = 7) -> dict[str, int]:
    """Request counts per payload table (multi-table union only)."""
    fqns = list(ctx.get("fqns") or [])
    if len(fqns) <= 1:
        return {}
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    window = max(1, min(90, int(days)))
    out: dict[str, int] = {}
    try:
        sql = (
            f"SELECT LOWER(TRIM(CAST(`_agentops_source_table` AS STRING))), COUNT(*) "
            f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {window} DAYS "
            "GROUP BY 1"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            for row in cur.fetchall() or []:
                if row[0]:
                    out[str(row[0]).lower()] = int(row[1] or 0)
    except Exception:
        return {}
    return out


def _table_node_usage(
    fqn: str,
    *,
    requests_7d: int | None = None,
    est_tokens_7d: int | None = None,
) -> dict[str, Any]:
    short = fqn.split(".")[-1] if fqn else "table"
    return {
        "fqn": fqn,
        "role": "Stores request/response JSON for each AI Gateway call",
        "requests_7d": requests_7d,
        "est_tokens_7d": est_tokens_7d,
        "used_by": [
            "Agents → Requests",
            "Cost (log-size proxy)",
            "Quality & trace detail",
            "Governance audit",
        ],
        "short_name": short,
    }


def _build_cost_tokens_hierarchy(
    fqns: list[str],
    *,
    table_counts: dict[str, int],
    table_tokens: dict[str, int],
    gw_rollup: dict[str, Any],
    billing: dict[str, Any],
    days: int = 7,
) -> dict[str, Any]:
    """Lineage for money + tokens: billing → gateway metering → payload log tables."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    bill_usd = float(billing.get("total_list_usd") or 0) if billing and not billing.get("error") else None
    bill_dbu = float(billing.get("total_dbu") or 0) if billing and not billing.get("error") else None
    gw_tok = int(gw_rollup.get("total_tokens") or 0) if gw_rollup and not gw_rollup.get("error") else None
    gw_req = int(gw_rollup.get("total_requests") or 0) if gw_rollup and not gw_rollup.get("error") else None

    bill_id = "src:billing-usage"
    nodes.append(
        {
            "id": bill_id,
            "kind": "billing_source",
            "label": "system.billing.usage",
            "detail": "MODEL_SERVING / AI_GATEWAY DBU × list_prices",
            "meta": (
                f"${bill_usd:.4f} est · {bill_dbu:.4f} DBU"
                if bill_usd is not None and bill_dbu is not None
                else "List-price estimate"
            ),
            "usage": {
                "role": "Workspace billing (real list price, not token guess)",
                "used_by": ["Cost → List price card", "MODEL SERVING DBU card"],
            },
        },
    )
    gw_id = "src:gateway-usage"
    nodes.append(
        {
            "id": gw_id,
            "kind": "usage_table",
            "label": "system.ai_gateway.usage",
            "detail": "Metered input + output tokens per request",
            "meta": (
                f"{gw_tok:,} tokens · {gw_req} reqs ({days}d)"
                if gw_tok is not None and gw_req is not None
                else "Gateway metering"
            ),
            "usage": {
                "role": "Official token counts from Databricks AI Gateway",
                "used_by": ["Cost → Gateway tokens", "Gateway tokens by model table"],
            },
        },
    )
    edges.append({"from": bill_id, "to": gw_id})

    schema_id = "layer:cost-schema"
    if fqns:
        schema_label = fqns[0].rsplit(".", 1)[0]
        nodes.append(
            {
                "id": schema_id,
                "kind": "schema",
                "label": schema_label,
                "detail": "Unity Catalog schema — inference payload tables",
                "meta": f"{len(fqns)} table(s)",
                "usage": {
                    "role": "Groups UC tables that AgentOps reads for logs",
                    "used_by": ["Cost proxy charts", "Governance", "Agents"],
                },
            },
        )
        edges.append({"from": gw_id, "to": schema_id})

    for fqn in sorted(fqns):
        tid = f"table:{fqn}"
        cnt = table_counts.get(fqn.lower())
        tok = table_tokens.get(fqn.lower())
        usage = _table_node_usage(fqn, requests_7d=cnt, est_tokens_7d=tok)
        meta_parts: list[str] = []
        if cnt is not None:
            meta_parts.append(f"{cnt} reqs")
        if tok is not None:
            meta_parts.append(f"~{tok:,} est tok")
        nodes.append(
            {
                "id": tid,
                "kind": "table",
                "label": fqn.split(".")[-1],
                "detail": fqn,
                "meta": " · ".join(meta_parts) if meta_parts else "Payload log",
                "usage": usage,
            },
        )
        if fqns:
            edges.append({"from": schema_id, "to": tid})
        else:
            edges.append({"from": gw_id, "to": tid})

    return {"nodes": nodes, "edges": edges}


def _build_telemetry_hierarchy_graph(
    fqns: list[str],
    runtime: dict[str, Any],
    uc_edges: list[dict[str, Any]],
    *,
    table_counts: dict[str, int] | None = None,
    table_tokens: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Hub-and-spoke graph: AI Gateway → schema → payload tables (+ optional UC upstream/downstream)."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    table_counts = table_counts or {}
    table_tokens = table_tokens or {}
    hub_set = {f.lower() for f in fqns}

    routes = runtime.get("routes") or []
    gw_url: str | None = None
    for r in routes:
        u = r.get("url")
        if isinstance(u, str) and u.strip():
            gw_url = u.strip()
            if "ai-gateway" in gw_url.lower():
                break
    if not gw_url and routes:
        u0 = routes[0].get("url")
        gw_url = str(u0).strip() if u0 else None

    gw_id = "layer:gateway"
    nodes.append(
        {
            "id": gw_id,
            "kind": "gateway",
            "label": "Databricks AI Gateway",
            "detail": gw_url or "chat/completions (from logs)",
            "meta": f"{int(runtime.get('total_requests') or 0)} requests ({runtime.get('window_days', 7)}d)",
        },
    )

    route_node_ids: list[str] = []
    for i, r in enumerate(routes[:12]):
        dest = r.get("destination_id") or r.get("api_type") or f"route-{i}"
        rid = f"route:{dest}"
        if any(n["id"] == rid for n in nodes):
            continue
        route_node_ids.append(rid)
        nodes.append(
            {
                "id": rid,
                "kind": "route",
                "label": str(dest),
                "detail": (r.get("url") or gw_url or "")[:200] or None,
                "meta": f"{int(r.get('requests') or 0)} reqs",
            },
        )
        edges.append({"from": gw_id, "to": rid})

    schema_label = fqns[0].rsplit(".", 1)[0] if fqns else "inference logs"
    hub_id = "layer:schema"
    nodes.append(
        {
            "id": hub_id,
            "kind": "schema",
            "label": schema_label,
            "detail": "Unity Catalog schema for AgentOps payload tables",
            "meta": f"{len(fqns)} payload table(s)",
        },
    )
    edges.append({"from": gw_id, "to": hub_id})
    if len(route_node_ids) == 1:
        edges.append({"from": route_node_ids[0], "to": hub_id})

    def _table_slug(fqn: str) -> str:
        return fqn.split(".")[-1].lower().replace("_payload", "")

    for fqn in sorted(fqns):
        tid = f"table:{fqn}"
        short = fqn.split(".")[-1]
        cnt = table_counts.get(fqn.lower())
        tok = table_tokens.get(fqn.lower())
        meta_parts: list[str] = []
        if cnt is not None:
            meta_parts.append(f"{cnt} reqs (7d)")
        if tok is not None:
            meta_parts.append(f"~{tok:,} est tok")
        nodes.append(
            {
                "id": tid,
                "kind": "table",
                "label": short,
                "detail": fqn,
                "meta": " · ".join(meta_parts) if meta_parts else None,
                "usage": _table_node_usage(fqn, requests_7d=cnt, est_tokens_7d=tok),
            },
        )
        edges.append({"from": hub_id, "to": tid})
        slug = _table_slug(fqn)
        for rid in route_node_ids:
            rlabel = rid.split(":", 1)[-1].lower()
            if slug and (slug in rlabel or rlabel in slug):
                edges.append({"from": rid, "to": tid})
        if not route_node_ids:
            edges.append({"from": gw_id, "to": tid})

    seen_uc: set[str] = set()
    for e in uc_edges:
        src = str(e.get("source") or "").strip()
        tgt = str(e.get("target") or "").strip()
        if not src or not tgt:
            continue
        sl, tl = src.lower(), tgt.lower()
        if tl in hub_set and sl not in hub_set:
            uid = f"uc-up:{sl}"
            if uid not in seen_uc:
                seen_uc.add(uid)
                nodes.append(
                    {
                        "id": uid,
                        "kind": "uc_upstream",
                        "label": src.split(".")[-1],
                        "detail": src,
                        "meta": e.get("entity_type"),
                    },
                )
            edges.append({"from": uid, "to": f"table:{tgt}"})
        if sl in hub_set and tl not in hub_set:
            did = f"uc-down:{tl}"
            if did not in seen_uc:
                seen_uc.add(did)
                nodes.append(
                    {
                        "id": did,
                        "kind": "uc_downstream",
                        "label": tgt.split(".")[-1],
                        "detail": tgt,
                        "meta": e.get("entity_type"),
                    },
                )
            edges.append({"from": f"table:{src}", "to": did})

    return {"nodes": nodes, "edges": edges}


def governance_lineage(limit: int = 80) -> dict[str, Any]:
    """UC system table lineage when enabled; graph-friendly edges."""
    ctx, err = _inference_table_ctx()
    if ctx:
        runtime = build_runtime_flow(ctx, days=7)
    else:
        runtime = build_runtime_flow_from_gateway(days=7)
        if err:
            runtime["payload_warning"] = str(err)[:300]
    table_counts = _per_table_request_counts(ctx, days=7) if ctx else {}
    table_tokens = _per_table_est_tokens(ctx, days=7) if ctx else {}
    gw_rollup_ct = ai_gateway_usage_rollup(7 * 24)
    billing_ct = billing_model_serving_cost(7 * 24)
    out: dict[str, Any] = {
        "inference_table": None,
        "edges": [],
        "nodes": [],
        "error": err if ctx else None,
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
        from app.services.inference import inference_fqn_list

        fqns_disc, _ = inference_fqn_list()
        out["inference_table_fqns"] = fqns_disc
        out["telemetry_hierarchy"] = _build_telemetry_hierarchy_graph(
            fqns_disc, runtime, [], table_counts={}, table_tokens={},
        )
        out["cost_tokens_hierarchy"] = _build_cost_tokens_hierarchy(
            fqns_disc,
            table_counts={},
            table_tokens={},
            gw_rollup=gw_rollup_ct,
            billing=billing_ct,
            days=7,
        )
        out["has_uc_lineage"] = False
        if err and not runtime.get("error"):
            out["error"] = None
            out["hint"] = (
                (out.get("hint") or "")
                + " Payload tables listed but not readable from SQL — graph uses AI Gateway traffic."
            ).strip()
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

    out["telemetry_hierarchy"] = _build_telemetry_hierarchy_graph(
        fqns,
        runtime,
        out.get("edges") or [],
        table_counts=table_counts,
        table_tokens=table_tokens,
    )
    out["cost_tokens_hierarchy"] = _build_cost_tokens_hierarchy(
        fqns,
        table_counts=table_counts,
        table_tokens=table_tokens,
        gw_rollup=gw_rollup_ct,
        billing=billing_ct,
        days=7,
    )
    from app.services.pii_scan import scan_recent_pii

    out["pii_scan"] = scan_recent_pii(days=7)
    out["has_uc_lineage"] = bool(
        out.get("edges")
        and any(
            (e.get("source") or "").lower() not in {f.lower() for f in fqns}
            or (e.get("target") or "").lower() not in {f.lower() for f in fqns}
            for e in out.get("edges") or []
            if isinstance(e, dict)
        )
    )
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
    lc = ctx.get("latency_col") or "latency_ms"
    sc_actual = ctx.get("status_col") or "status_code"
    dest_actual = ctx.get("dest_col") or "destination_id"
    sql = (
        f"SELECT `request_id`, `{tc}`, {req_sel} AS requester, `{sc_actual}`, `{lc}`, `{dest_actual}` "
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


def _merge_gateway_quality_baselines(dst: dict[str, Any], gw: dict[str, Any]) -> None:
    """Fill null inference-table aggregates from AI Gateway for the same hours window."""
    if gw.get("error"):
        return
    sparse = dst.get("p50_latency_ms") is None and dst.get("avg_latency_ms") is None
    if not sparse:
        return
    for k in ("avg_latency_ms", "p50_latency_ms", "p95_latency_ms"):
        if dst.get(k) is None and gw.get(k) is not None:
            dst[k] = gw[k]
    if dst.get("error_rate_pct") is None and gw.get("error_rate_pct") is not None:
        dst["error_rate_pct"] = gw["error_rate_pct"]
    dst["fallback_gateway_metrics"] = True


def quality_observability(hours: int = 168) -> dict[str, Any]:
    """Production quality proxies without MLflow (latency stability, errors, reasoning presence)."""
    h = max(1, min(24 * 90, int(hours)))
    gw_snap = quality_observability_from_gateway(h)
    ctx, err = _inference_table_ctx()
    out: dict[str, Any] = {
        "window_hours": int(h),
        "avg_latency_ms": None,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "error_rate_pct": None,
        "requests_sampled_for_json": 0,
        "responses_with_reasoning_pct": None,
        "error": err,
        "source": "inference_table",
        "fallback_gateway_metrics": False,
        "inference_notes": "",
        "latency_column_used": None,
        "response_column_used": None,
    }
    if err or not ctx:
        note = (
            "Payload inference tables unavailable — latency/errors from AI Gateway only "
            "(no response-body reasoning sampling)."
        )
        gw_snap["note"] = note
        gw_snap["fallback_gateway_metrics"] = True
        return gw_snap
    tbl = ctx["table_sql"]
    tc = ctx["time_col"]
    lc = ctx["latency_col"]
    sc = ctx["status_col"]
    tx = _ctx_test_excl(ctx)
    cmap = {c.lower(): c for c in ctx["cols"]}
    rsp_alias = ctx.get("response_body_col")
    if not rsp_alias:
        for cand in RESPONSE_BODY_CANDIDATES:
            hit = cmap.get(cand.lower())
            if hit:
                rsp_alias = hit
                break
    out["latency_column_used"] = lc
    out["response_column_used"] = rsp_alias
    if not lc:
        out["error"] = "latency column not found"
        _merge_gateway_quality_baselines(out, gw_snap)
        if out["fallback_gateway_metrics"]:
            out["source"] = "gateway_supplement"
            out["inference_notes"] = "No recognizable latency column in payload schema; showing gateway aggregates."
            out["error"] = None
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
            f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {h} HOURS{tx}"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
        if row:
            out["avg_latency_ms"] = float(row[0]) if row[0] is not None else None
            out["p50_latency_ms"] = float(row[1]) if row[1] is not None else None
            out["p95_latency_ms"] = float(row[2]) if row[2] is not None else None
            out["error_rate_pct"] = float(row[3]) * 100.0 if row[3] is not None else None

        if rsp_alias:
            sql_sample = (
                f"SELECT CAST(`{rsp_alias}` AS STRING) AS rsp FROM {tbl} "
                f"WHERE `{tc}` >= current_timestamp() - INTERVAL {h} HOURS{tx} "
                f"AND `{rsp_alias}` IS NOT NULL LIMIT 50"
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
        else:
            out["inference_notes"] = (
                (out.get("inference_notes") or "").strip()
                + " No standard response JSON column detected — skipping reasoning heuristic."
            ).strip()

        _merge_gateway_quality_baselines(out, gw_snap)
        if out["fallback_gateway_metrics"]:
            suffix = (
                " Latency / error rate supplemented from system.ai_gateway.usage "
                f"because the inference log window ({h}h, after filters) had no rollup values."
            )
            out["inference_notes"] = (out.get("inference_notes") or "").strip() + suffix

        out["error"] = None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e).strip()[:500]
        _merge_gateway_quality_baselines(out, gw_snap)
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
    tx = _ctx_test_excl(ctx)
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
        f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {int(days)} DAYS{tx} "
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
