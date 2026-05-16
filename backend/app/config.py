"""Environment-driven settings. Secrets only via env / Databricks secrets — never committed."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    databricks_host: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_HOST", "DATABRICKS_SERVER_HOSTNAME"),
        description="Workspace host without scheme, e.g. dbc-xxxx.cloud.databricks.com",
    )
    databricks_http_path: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_HTTP_PATH"),
        description="Warehouse HTTP path, e.g. /sql/1.0/warehouses/abc",
    )
    databricks_token: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_TOKEN", "DATABRICKS_PAT"),
    )
    workspace_id: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_WORKSPACE_ID", "WORKSPACE_ID"),
    )

    #: Optional fully qualified inference log table for aggregates (catalog.schema.table)
    inference_table_fqn: str = Field(
        default="",
        validation_alias=AliasChoices("AGENTOPS_INFERENCE_TABLE", "INFERENCE_TABLE_FQN"),
    )

    #: Optional comma-separated FQNs — same schema across tables; queried as UNION ALL (one logical stream)
    inference_tables_fqn: str = Field(
        default="",
        validation_alias=AliasChoices("AGENTOPS_INFERENCE_TABLES"),
    )

    #: Unity Catalog catalog.schema — discover payload tables automatically (optional table suffix filter)
    inference_schema_fqn: str = Field(
        default="",
        validation_alias=AliasChoices("AGENTOPS_INFERENCE_SCHEMA"),
    )

    #: When using schema discovery: only include tables whose name ends with this suffix (empty = all base tables)
    inference_table_name_suffix: str = Field(
        default="_payload",
        validation_alias=AliasChoices("AGENTOPS_INFERENCE_TABLE_SUFFIX"),
    )

    #: Explicit event-time column if auto-detect fails (must match Delta column name)
    inference_time_column: str = Field(
        default="",
        validation_alias=AliasChoices("AGENTOPS_INFERENCE_TIME_COLUMN"),
    )

    #: Optional Delta column (logical name) — same value groups rows for cross-model compare
    inference_comparison_group_column: str = Field(
        default="comparison_group_id",
        validation_alias=AliasChoices("AGENTOPS_COMPARISON_GROUP_COLUMN"),
    )

    #: JSON array of replay targets: [{"id":"a","label":"…","url":"https://…/v1/chat/completions","headers":{}}]
    replay_targets_json: str = Field(
        default="",
        validation_alias=AliasChoices("AGENTOPS_REPLAY_TARGETS_JSON"),
    )

    #: Optional path to a UTF-8 JSON file (same array schema). Use when .env JSON is hard to escape on Windows.
    replay_targets_file: str = Field(
        default="",
        validation_alias=AliasChoices("AGENTOPS_REPLAY_TARGETS_FILE"),
    )

    #: When true, POST /api/v1/benchmark/prompt can fan out a user prompt to replay targets (security-sensitive).
    benchmark_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("AGENTOPS_BENCHMARK_ENABLED"),
    )

    #: Max replay targets per benchmark call (caps cost / blast radius).
    benchmark_max_targets: int = Field(
        default=6,
        validation_alias=AliasChoices("AGENTOPS_BENCHMARK_MAX_TARGETS"),
    )

    #: Optional — only for emergency UI demos; keep **false** for real telemetry
    use_demo_metrics: bool = Field(
        default=False,
        validation_alias=AliasChoices("AGENTOPS_USE_DEMO_METRICS"),
    )


def get_settings() -> Settings:
    """Fresh read from env / backend/.env so edits apply without restarting the worker (dev-friendly)."""
    return Settings()


# Call get_settings() after load_dotenv() in app.main (do not eager-import at module import).
