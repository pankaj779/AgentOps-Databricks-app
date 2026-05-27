"""Compact workspace status for the UI banner."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.agent_unify import unified_agents_catalog
from app.services.app_config_store import effective_exclude_test_requests, get_user_settings
from app.services.databricks_status import sql_probe
from app.services.inference import inference_fqn_list


def workspace_status_summary() -> dict[str, Any]:
    db = sql_probe()
    agent_count = 0
    try:
        cat = unified_agents_catalog()
        agent_count = len(cat.get("agents") or [])
    except Exception:  # noqa: BLE001
        pass
    try:
        fqns, fq_note = inference_fqn_list()
    except Exception:  # noqa: BLE001
        fqns, fq_note = [], "inference tables unavailable"
    settings = get_user_settings()
    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "sql_ok": bool(db.get("sql_reachable")),
        "sql_error": db.get("last_error"),
        "agent_count": agent_count,
        "payload_table_count": len(fqns),
        "fqns_note": fq_note,
        "environment": "databricks-sql" if db.get("configured") else "disconnected",
        "exclude_test_requests": settings["exclude_test_requests"],
        "host": db.get("host"),
    }
