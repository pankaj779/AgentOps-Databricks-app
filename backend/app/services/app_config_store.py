"""Lightweight app preferences (SQLite). Lakebase-ready pattern for team settings."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.config import BACKEND_ROOT, get_settings

_DB_PATH = BACKEND_ROOT / "agentops_app_config.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH), timeout=5.0)
    c.execute(
        "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)",
    )
    return c


def get_kv(key: str, default: Any = None) -> Any:
    try:
        with _conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(row[0])
    except (OSError, sqlite3.Error, json.JSONDecodeError):
        return default


def set_kv(key: str, value: Any) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), time.time()),
        )


def effective_exclude_test_requests() -> bool:
    """User toggle overrides env default when set in store."""
    if get_kv("exclude_test_requests") is not None:
        return bool(get_kv("exclude_test_requests"))
    return bool(get_settings().exclude_test_requests_from_analytics)


def get_user_settings() -> dict[str, Any]:
    return {
        "exclude_test_requests": effective_exclude_test_requests(),
        "default_compare_prompt": get_kv("default_compare_prompt", "What is Databricks?"),
        "pinned_dashboard_note": get_kv("pinned_dashboard_note"),
    }


def patch_user_settings(patch: dict[str, Any]) -> dict[str, Any]:
    if "exclude_test_requests" in patch:
        set_kv("exclude_test_requests", bool(patch["exclude_test_requests"]))
    if "default_compare_prompt" in patch and patch["default_compare_prompt"] is not None:
        set_kv("default_compare_prompt", str(patch["default_compare_prompt"])[:2000])
    if "pinned_dashboard_note" in patch:
        set_kv("pinned_dashboard_note", str(patch["pinned_dashboard_note"])[:500] if patch["pinned_dashboard_note"] else None)
    return get_user_settings()
