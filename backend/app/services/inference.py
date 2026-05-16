"""Read inference / payload Delta tables via SQL warehouse (real telemetry)."""

from __future__ import annotations

import time
from typing import Any

from app.config import get_settings
from app.databricks.fqn import quote_fqn, quote_schema_fqn, validate_fqn, validate_schema_fqn
from app.databricks.sql_client import sql_connection

TIME_COLUMN_CANDIDATES: tuple[str, ...] = (
    "request_time",
    "timestamp",
    "event_time",
    "created_at",
    "time",
    "inference_timestamp",
    "request_timestamp",
    "log_time",
    "_timestamp",
    "event_timestamp",
    "date",
    "request_ts",
)

GROUP_KEY_CANDIDATES: tuple[str, ...] = (
    "_agentops_source_table",
    "endpoint_name",
    "model_name",
    "model_id",
    "deployment_name",
    "served_model_name",
    "databricks_model_name",
    "gateway_endpoint",
    "destination_id",  # Unity AI Gateway inference payload tables
    "agent_name",
)

LATENCY_CANDIDATES: tuple[str, ...] = (
    "latency_ms",
    "response_time_ms",
    "duration_ms",
    "total_latency_ms",
    "latency",
)

STATUS_CANDIDATES: tuple[str, ...] = (
    "status_code",
    "http_status",
    "response_status",
    "status",
)


def _pick_column(available: set[str], candidates: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in available}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


_SCHEMA_DISCOVER_CACHE: dict[str, tuple[float, list[str], str | None]] = {}
_SCHEMA_DISCOVER_TTL = 120.0


def _discover_tables_uncached(catalog: str, schema: str) -> tuple[list[str], str | None]:
    """List base table names in a UC schema (information_schema first, then SHOW TABLES)."""
    c_esc = catalog.replace("'", "''")
    s_esc = schema.replace("'", "''")
    sql_info = (
        "SELECT table_name FROM system.information_schema.tables "
        f"WHERE lower(table_catalog) = lower('{c_esc}') AND lower(table_schema) = lower('{s_esc}') "
        "AND table_type = 'BASE TABLE' ORDER BY table_name"
    )
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_info)
            rows = cur.fetchall() or []
        if rows:
            return [str(r[0]) for r in rows if r and r[0] is not None], None
    except Exception:  # noqa: BLE001
        pass
    try:
        qs = quote_schema_fqn(f"{catalog}.{schema}")
        sql_show = f"SHOW TABLES IN {qs}"
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_show)
            rows = cur.fetchall() or []
        names: list[str] = []
        for r in rows:
            if not r:
                continue
            if len(r) >= 3 and r[2] is True:
                continue
            tname: str | None = None
            if len(r) >= 2 and r[1] is not None:
                tname = str(r[1]).strip()
            elif r[0] is not None:
                tname = str(r[0]).strip()
            if tname and tname.lower() != "tablename":
                names.append(tname)
        return names, None
    except Exception as e:  # noqa: BLE001
        return [], str(e).strip()[:400]


def discover_inference_table_fqns() -> tuple[list[str], str | None]:
    """Tables under AGENTOPS_INFERENCE_SCHEMA matching optional name suffix (cached)."""
    s = get_settings()
    schema_raw = (s.inference_schema_fqn or "").strip()
    if not schema_raw:
        return [], None
    if not validate_schema_fqn(schema_raw):
        return [], "AGENTOPS_INFERENCE_SCHEMA must be catalog.schema"
    cat, sch = schema_raw.split(".", 1)
    suffix = (s.inference_table_name_suffix or "").strip()
    cache_key = f"{cat}.{sch}|{suffix}"
    now = time.monotonic()
    hit = _SCHEMA_DISCOVER_CACHE.get(cache_key)
    if hit is not None and (now - hit[0]) < _SCHEMA_DISCOVER_TTL:
        return list(hit[1]), hit[2]

    raw_names, err = _discover_tables_uncached(cat, sch)
    if err:
        _SCHEMA_DISCOVER_CACHE[cache_key] = (now, [], err)
        return [], err
    fqns: list[str] = []
    for t in raw_names:
        if suffix and not t.endswith(suffix):
            continue
        fqn = f"{cat}.{sch}.{t}"
        if validate_fqn(fqn):
            fqns.append(fqn)
    fqns.sort()
    warn = None if fqns else "No tables found matching suffix; check AGENTOPS_INFERENCE_TABLE_SUFFIX or schema name."
    _SCHEMA_DISCOVER_CACHE[cache_key] = (now, fqns, warn)
    return fqns, warn


def resolve_source_table_fqn(requested: str | None) -> str | None:
    """Map user/API fragment to a configured inference table FQN, or None if not allowed."""
    if not requested or not str(requested).strip():
        return None
    raw = str(requested).strip()
    fqns, _ = inference_fqn_list()
    if not fqns:
        return None
    rl = raw.lower()
    for f in fqns:
        if f.lower() == rl:
            return f
        if f.split(".")[-1].lower() == rl:
            return f
    return None


def inference_fqn_list() -> tuple[list[str], str | None]:
    """Resolved UC tables and optional resolution note.

    Precedence: AGENTOPS_INFERENCE_TABLES → AGENTOPS_INFERENCE_SCHEMA (discover) → AGENTOPS_INFERENCE_TABLE.
    """
    s = get_settings()
    csv = (s.inference_tables_fqn or "").strip()
    if csv:
        out: list[str] = []
        for chunk in csv.replace("\n", ",").replace(";", ",").split(","):
            t = chunk.strip()
            if t and validate_fqn(t):
                out.append(t)
        return out, ("explicit_tables" if out else "no valid entries in AGENTOPS_INFERENCE_TABLES")

    schema_note = (s.inference_schema_fqn or "").strip()
    if schema_note:
        fqns, w = discover_inference_table_fqns()
        return fqns, w or ("schema_discovery" if fqns else None)

    raw = (s.inference_table_fqn or "").strip()
    if raw and validate_fqn(raw):
        return [raw], None
    return [], None


def inference_query_table_sql(fqns: list[str]) -> str:
    """SQL FROM target: one quoted table or parenthesized UNION ALL (identical column layouts)."""
    if len(fqns) == 1:
        return quote_fqn(fqns[0])
    branches: list[str] = []
    for f in fqns:
        q = quote_fqn(f)
        safe = f.replace("'", "''")
        branches.append(f"SELECT *, '{safe}' AS _agentops_source_table FROM {q}")
    parts = " UNION ALL ".join(branches)
    return f"({parts}) AS agentops_inference_union"


_DESCRIBE_CACHE: dict[str, tuple[float, tuple[list[str] | None, str | None]]] = {}
_DESCRIBE_TTL_SEC = 120.0


def describe_columns(table_sql: str) -> tuple[list[str] | None, str | None]:
    """Return column names from DESCRIBE TABLE, or (None, error). Cached ~2m per table."""
    now = time.monotonic()
    hit = _DESCRIBE_CACHE.get(table_sql)
    if hit is not None and (now - hit[0]) < _DESCRIBE_TTL_SEC:
        return hit[1]
    out = _describe_columns_uncached(table_sql)
    _DESCRIBE_CACHE[table_sql] = (now, out)
    return out


def _describe_columns_uncached(table_sql: str) -> tuple[list[str] | None, str | None]:
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"DESCRIBE TABLE {table_sql}")
            rows = cur.fetchall()
            cols: list[str] = []
            for r in rows:
                if r and r[0]:
                    cols.append(str(r[0]))
            return cols, None
    except Exception as e:  # noqa: BLE001
        return None, str(e).strip()[:400]


def resolve_time_column(columns: list[str], explicit: str) -> str | None:
    if explicit.strip():
        e = explicit.strip()
        if e in columns:
            return e
        # case-insensitive
        cmap = {c.lower(): c for c in columns}
        if e.lower() in cmap:
            return cmap[e.lower()]
    avail = set(columns)
    return _pick_column(avail, TIME_COLUMN_CANDIDATES)


def count_since_hours(table_sql: str, time_col: str, hours: int = 24) -> tuple[int | None, str | None]:
    try:
        sql = (
            f"SELECT COUNT(*) AS c FROM {table_sql} "
            f"WHERE `{time_col}` >= current_timestamp() - INTERVAL {hours} HOURS"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
            if row and row[0] is not None:
                return int(row[0]), None
    except Exception as e:  # noqa: BLE001
        return None, str(e).strip()[:400]
    return None, None


def inference_metrics() -> dict[str, Any]:
    """Counts + resolved columns for overview (no secrets)."""
    s = get_settings()
    fqns, fqns_note = inference_fqn_list()
    out: dict[str, Any] = {
        "table_configured": bool(fqns),
        "table_fqn": fqns[0] if len(fqns) == 1 else None,
        "table_fqns": fqns if len(fqns) > 1 else None,
        "fqns_resolution_note": fqns_note,
        "time_column": None,
        "count_24h": None,
        "count_7d": None,
        "describe_error": None,
        "count_error": None,
    }
    if not fqns:
        hint = "Set AGENTOPS_INFERENCE_SCHEMA=catalog.schema and optional AGENTOPS_INFERENCE_TABLE_SUFFIX (default _payload), or AGENTOPS_INFERENCE_TABLE, or AGENTOPS_INFERENCE_TABLES."
        single = (s.inference_table_fqn or "").strip()
        multi = (s.inference_tables_fqn or "").strip()
        schema = (s.inference_schema_fqn or "").strip()
        if single or multi or schema:
            out["describe_error"] = (
                "Invalid or empty inference config — check FQNs / schema. "
                f"Resolution note: {fqns_note or 'none'}"
            )
        else:
            out["describe_error"] = hint
        return out

    try:
        table_sql = inference_query_table_sql(fqns)
        describe_sql = quote_fqn(fqns[0])
    except ValueError as e:
        out["describe_error"] = str(e)
        return out

    cols, derr = describe_columns(describe_sql)
    if derr:
        out["describe_error"] = derr
        return out
    if not cols:
        out["describe_error"] = "DESCRIBE returned no columns"
        return out

    time_col = resolve_time_column(cols, s.inference_time_column)
    out["time_column"] = time_col
    out["columns_sample"] = cols[:60]

    if not time_col:
        out["count_error"] = (
            "No time column found. Set AGENTOPS_INFERENCE_TIME_COLUMN to your event time column "
            f"(available sample: {', '.join(cols[:12])}{'…' if len(cols) > 12 else ''})"
        )
        return out

    c24, err = count_since_hours(table_sql, time_col, 24)
    out["count_24h"] = c24
    if err:
        out["count_error"] = err
        return out

    stat_col = _pick_column(set(cols), STATUS_CANDIDATES)
    if stat_col:
        er = inference_error_rate_24h(time_col, table_sql, stat_col)
        if er is not None:
            out["error_rate_pct_24h"] = er

    c7, _ = count_since_hours(table_sql, time_col, 24 * 7)
    out["count_7d"] = c7

    return out


def inference_error_rate_24h(time_col: str, table_sql: str, status_col: str | None) -> float | None:
    if not status_col:
        return None
    try:
        # Numeric HTTP-style codes: 4xx/5xx = error
        sql = (
            f"SELECT AVG(CASE WHEN CAST(`{status_col}` AS DOUBLE) >= 400 "
            f"OR CAST(`{status_col}` AS DOUBLE) < 100 THEN 1.0 ELSE 0.0 END) AS err_rate "
            f"FROM {table_sql} WHERE `{time_col}` >= current_timestamp() - INTERVAL 24 HOURS"
        )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0]) * 100.0
    except Exception:
        return None
    return None


def _stub_rollups_per_table(fqns: list[str], group_col: str) -> list[dict[str, Any]]:
    """One dashboard row per payload table when the 24h window has no data or query failed for multi-table mode."""
    out: list[dict[str, Any]] = []
    for f in fqns:
        short = f.split(".")[-1]
        out.append(
            {
                "id": f[:128],
                "name": short[:128] or "unknown",
                "requests_24h": 0,
                "rpm": 0.0,
                "p95_latency_ms": 0.0,
                "error_rate_pct": 0.0,
                "group_column": group_col,
            },
        )
    return out


def _merge_rollups_missing_source_tables(
    rollups: list[dict[str, Any]],
    fqns: list[str],
    group_col: str,
) -> None:
    """Append zero-traffic payload tables missing from a 24h GROUP BY (common with idle models)."""
    if len(fqns) <= 1 or group_col != "_agentops_source_table":
        return
    present: set[str] = set()
    for o in rollups:
        present.add(o.get("id", "").strip().lower())
        present.add(o.get("name", "").strip().lower())
    for f in fqns:
        fn = f.strip()
        if not fn:
            continue
        fl = fn.lower()
        short = fn.split(".")[-1].lower()
        if fl in present or short in present:
            continue
        rollups.append(
            {
                "id": fn[:128],
                "name": (fn.split(".")[-1] or fn)[:128],
                "requests_24h": 0,
                "rpm": 0.0,
                "p95_latency_ms": 0.0,
                "error_rate_pct": 0.0,
                "group_column": group_col,
            },
        )
        present.add(fl)
        present.add(short)


def inference_agent_rollups(limit: int = 15) -> list[dict[str, Any]]:
    """GROUP BY heuristic agent / model column — real aggregates."""
    s = get_settings()
    fqns, _note = inference_fqn_list()
    if not fqns:
        return []

    try:
        table_sql = inference_query_table_sql(fqns)
        describe_sql = quote_fqn(fqns[0])
    except ValueError:
        return _stub_rollups_per_table(fqns, "_agentops_source_table") if len(fqns) > 1 else []

    cols, derr = describe_columns(describe_sql)
    if derr or not cols:
        return _stub_rollups_per_table(fqns, "_agentops_source_table") if len(fqns) > 1 else []

    time_col = resolve_time_column(cols, s.inference_time_column)
    if not time_col:
        return _stub_rollups_per_table(fqns, "_agentops_source_table") if len(fqns) > 1 else []

    avail = set(cols)
    if len(fqns) > 1:
        avail.add("_agentops_source_table")
    group_col = _pick_column(avail, GROUP_KEY_CANDIDATES)
    if not group_col:
        return _stub_rollups_per_table(fqns, "_agentops_source_table") if len(fqns) > 1 else []

    lat_col = _pick_column(avail, LATENCY_CANDIDATES)
    stat_col = _pick_column(avail, STATUS_CANDIDATES)

    # RPM from 24h window: count / (24 * 60)
    try:
        if lat_col and stat_col:
            sql = (
                f"SELECT `{group_col}` AS k, COUNT(*) AS cnt, "
                f"approx_percentile(`{lat_col}`, 0.95) AS p95, "
                f"AVG(CASE WHEN CAST(`{stat_col}` AS DOUBLE) >= 400 "
                f"OR CAST(`{stat_col}` AS DOUBLE) < 100 THEN 1.0 ELSE 0.0 END) AS err_frac "
                f"FROM {table_sql} WHERE `{time_col}` >= current_timestamp() - INTERVAL 24 HOURS "
                f"GROUP BY `{group_col}` ORDER BY cnt DESC LIMIT {int(limit)}"
            )
        elif lat_col:
            sql = (
                f"SELECT `{group_col}` AS k, COUNT(*) AS cnt, "
                f"approx_percentile(`{lat_col}`, 0.95) AS p95 "
                f"FROM {table_sql} WHERE `{time_col}` >= current_timestamp() - INTERVAL 24 HOURS "
                f"GROUP BY `{group_col}` ORDER BY cnt DESC LIMIT {int(limit)}"
            )
        else:
            sql = (
                f"SELECT `{group_col}` AS k, COUNT(*) AS cnt "
                f"FROM {table_sql} WHERE `{time_col}` >= current_timestamp() - INTERVAL 24 HOURS "
                f"GROUP BY `{group_col}` ORDER BY cnt DESC LIMIT {int(limit)}"
            )
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
    except Exception:
        return _stub_rollups_per_table(fqns, group_col) if len(fqns) > 1 else []

    out: list[dict[str, Any]] = []
    for r in rows:
        if not r or r[0] is None:
            continue
        k = str(r[0])
        cnt = int(r[1]) if len(r) > 1 and r[1] is not None else 0
        rpm = cnt / (24.0 * 60.0)
        p95 = float(r[2]) if len(r) > 2 and r[2] is not None else 0.0
        err_pct = float(r[3]) * 100.0 if len(r) > 3 and r[3] is not None else 0.0
        out.append(
            {
                "id": k[:128],
                "name": k[:128] or "unknown",
                "requests_24h": cnt,
                "rpm": rpm,
                "p95_latency_ms": p95,
                "error_rate_pct": err_pct,
                "group_column": group_col,
            }
        )
    _merge_rollups_missing_source_tables(out, fqns, group_col)
    if not out and len(fqns) > 1:
        return _stub_rollups_per_table(fqns, group_col)
    return out
