"""Probe SQL warehouse connectivity and light metadata (no secrets in responses)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.databricks.sql_client import sql_connection, warehouse_id_from_http_path


def _safe_error_message(exc: BaseException) -> str:
    msg = str(exc).strip()
    if len(msg) > 280:
        msg = msg[:280] + "…"
    for needle in ("dapi", "Bearer ", "Authorization"):
        if needle.lower() in msg.lower():
            return "SQL connection failed (details redacted)."
    return msg or "SQL connection failed."


def sql_probe() -> dict[str, Any]:
    """Return a JSON-serializable status dict for /api/health."""
    s = get_settings()
    host = s.databricks_host.strip()
    path = s.databricks_http_path.strip()
    wid = warehouse_id_from_http_path(path)
    configured = bool(host and path and s.databricks_token)

    base: dict[str, Any] = {
        "configured": configured,
        "host": host or None,
        "warehouse_id": wid,
        "workspace_id": s.workspace_id or None,
        "sql_reachable": None,
        "server_time_utc": None,
        "catalogs_sample": None,
        "last_error": None,
    }

    if not configured:
        base["last_error"] = None
        base["sql_reachable"] = False
        return base

    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_timestamp()")
            row = cur.fetchone()
            ts = row[0] if row else None
            if hasattr(ts, "replace"):
                base["server_time_utc"] = ts.replace(tzinfo=UTC).isoformat()
            else:
                base["server_time_utc"] = datetime.now(tz=UTC).isoformat()

            try:
                cur.execute("SHOW CATALOGS")
                catalogs: list[str] = []
                for r in cur.fetchmany(8):
                    if r and r[0]:
                        catalogs.append(str(r[0]))
                base["catalogs_sample"] = catalogs or None
            except Exception:  # noqa: BLE001
                base["catalogs_sample"] = None
            base["sql_reachable"] = True
    except Exception as e:  # noqa: BLE001
        base["sql_reachable"] = False
        base["last_error"] = _safe_error_message(e)

    return base
