"""Databricks SQL Warehouse sessions via PAT (dev) or OAuth token (Apps)."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from app.config import get_settings

try:
    from databricks import sql as dbsql
except ImportError as e:  # pragma: no cover
    dbsql = None  # type: ignore[assignment]
    _import_error = e
else:
    _import_error = None


@contextmanager
def sql_connection() -> Iterator:
    if dbsql is None:
        raise RuntimeError(
            "databricks-sql-connector is not installed. pip install databricks-sql-connector"
        ) from _import_error
    s = get_settings()
    if not s.databricks_host or not s.databricks_http_path:
        raise ValueError("DATABRICKS_HOST and DATABRICKS_HTTP_PATH must be set")
    if not s.databricks_token:
        raise ValueError("DATABRICKS_TOKEN is not set")
    conn = dbsql.connect(
        server_hostname=s.databricks_host,
        http_path=s.databricks_http_path,
        access_token=s.databricks_token,
    )
    try:
        yield conn
    finally:
        conn.close()


def warehouse_id_from_http_path(http_path: str) -> str | None:
    if not http_path:
        return None
    parts = http_path.rstrip("/").split("/")
    return parts[-1] if parts else None
