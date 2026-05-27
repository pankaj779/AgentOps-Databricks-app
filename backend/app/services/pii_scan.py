"""Lightweight PII heuristic scan on recent inference request bodies."""

from __future__ import annotations

import re
from typing import Any

from app.services.analytics import _inference_table_ctx, _ctx_test_excl


_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


def scan_recent_pii(*, days: int = 7, limit: int = 500) -> dict[str, Any]:
    ctx, err = _inference_table_ctx()
    if err or not ctx:
        return {"matches": 0, "samples": [], "error": err, "scanned_rows": 0}
    cmap = {c.lower(): c for c in ctx["cols"]}
    rq = cmap.get("request")
    rqid = cmap.get("request_id")
    if not rq:
        return {"matches": 0, "samples": [], "error": "no request column", "scanned_rows": 0}
    tc = ctx["time_col"]
    tbl = ctx["table_sql"]
    tx = _ctx_test_excl(ctx)
    lim = max(10, min(2000, int(limit)))
    sql = (
        f"SELECT CAST(`{rqid}` AS STRING), SUBSTRING(CAST(`{rq}` AS STRING), 1, 4000) "
        f"FROM {tbl} WHERE `{tc}` >= current_timestamp() - INTERVAL {int(days)} DAYS{tx} "
        f"ORDER BY `{tc}` DESC NULLS LAST LIMIT {lim}"
    )
    from app.databricks.sql_client import sql_connection

    matches = 0
    samples: list[dict[str, Any]] = []
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
        for row in rows:
            rid, body = row[0], str(row[1] or "")
            kinds: list[str] = []
            if _EMAIL.search(body):
                kinds.append("email")
            if _SSN.search(body):
                kinds.append("ssn_pattern")
            if _PHONE.search(body):
                kinds.append("phone")
            if kinds:
                matches += 1
                if len(samples) < 8:
                    samples.append(
                        {
                            "request_id": str(rid) if rid else None,
                            "kinds": kinds,
                            "preview": body[:120].replace("\n", " "),
                        },
                    )
        return {
            "matches": matches,
            "samples": samples,
            "error": None,
            "scanned_rows": len(rows),
            "window_days": days,
            "note": "Regex scan only — not a full DLP engine.",
        }
    except Exception as e:  # noqa: BLE001
        return {"matches": 0, "samples": [], "error": str(e)[:400], "scanned_rows": 0}
