"""Compose overview KPIs from Databricks probes + optional inference table."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.analytics import (
    ai_gateway_token_snapshots,
    ai_gateway_usage_rollup,
    quality_observability,
    quality_score_from_observability,
)
from app.services.databricks_status import sql_probe
from app.services.inference import (
    count_since_hours,
    discovered_payload_table_count,
    inference_agent_rollups,
    inference_fqn_list,
    inference_metrics,
    inference_query_table_sql,
)


class OverviewDTO(BaseModel):
    generated_at: datetime
    environment: str
    data_mode: str = Field(description="demo | live | live_partial | disconnected")
    agents_monitored: int
    requests_24h: int
    requests_24h_source: str = Field(
        description="demo | inference_table | ai_gateway | none | synthetic",
    )
    error_rate_pct: float
    est_monthly_cost_usd: float | None
    quality_score_avg: float | None
    databricks_sql_reachable: bool | None = None
    inference_setup_hint: str | None = None
    count_7d: int | None = None
    gateway_tokens_24h: int | None = None
    gateway_tokens_7d: int | None = None
    gateway_usage_error: str | None = None
    window_hours: int = 168
    count_window: int | None = None
    gateway_tokens_window: int | None = None


def _synthetic_overview(probe: dict, configured: bool) -> OverviewDTO:
    reachable = probe.get("sql_reachable")
    env = "local-demo"
    if configured:
        env = "databricks-sql" if reachable is True else "databricks-sql-error"
    return OverviewDTO(
        generated_at=datetime.now(tz=UTC),
        environment=env,
        data_mode="demo",
        agents_monitored=3,
        requests_24h=12_400,
        requests_24h_source="synthetic",
        error_rate_pct=0.42,
        est_monthly_cost_usd=1840.0,
        quality_score_avg=0.91,
        databricks_sql_reachable=reachable if configured else None,
        inference_setup_hint="AGENTOPS_USE_DEMO_METRICS is on — set to false for real inference data only.",
        count_7d=None,
        gateway_tokens_24h=None,
        gateway_tokens_7d=None,
        gateway_usage_error=None,
    )


def _hint_from_inference(inf: dict) -> str | None:
    if inf.get("describe_error"):
        return str(inf["describe_error"])
    if inf.get("count_error"):
        return str(inf["count_error"])
    return None


def build_overview(*, window_hours: int = 168) -> OverviewDTO:
    wh = max(1, min(24 * 90, int(window_hours)))
    s = get_settings()
    probe = sql_probe()
    reachable = probe.get("sql_reachable")
    configured = bool(probe.get("configured"))

    if s.use_demo_metrics:
        return _synthetic_overview(probe, configured)

    env = "local-demo"
    if configured:
        env = "databricks-sql" if reachable is True else "databricks-sql-error"

    if not configured:
        return OverviewDTO(
            generated_at=datetime.now(tz=UTC),
            environment=env,
            data_mode="demo",
            agents_monitored=3,
            requests_24h=12_400,
            requests_24h_source="demo",
            error_rate_pct=0.42,
            est_monthly_cost_usd=1840.0,
            quality_score_avg=0.91,
            databricks_sql_reachable=None,
            inference_setup_hint="Configure DATABRICKS_* in backend/.env, then AGENTOPS_INFERENCE_SCHEMA or AGENTOPS_INFERENCE_TABLE.",
            count_7d=None,
            gateway_tokens_24h=None,
            gateway_tokens_7d=None,
            gateway_usage_error=None,
        )

    if reachable is not True:
        return OverviewDTO(
            generated_at=datetime.now(tz=UTC),
            environment=env,
            data_mode="disconnected",
            agents_monitored=0,
            requests_24h=0,
            requests_24h_source="none",
            error_rate_pct=0.0,
            est_monthly_cost_usd=None,
            quality_score_avg=None,
            databricks_sql_reachable=False,
            inference_setup_hint="Fix SQL warehouse connection (see /api/health databricks.last_error).",
            count_7d=None,
            gateway_tokens_24h=None,
            gateway_tokens_7d=None,
            gateway_usage_error=None,
        )

    inf = inference_metrics()
    hint = _hint_from_inference(inf)
    c24 = inf.get("count_24h")
    c7 = inf.get("count_7d")
    err_pct = float(inf["error_rate_pct_24h"]) if inf.get("error_rate_pct_24h") is not None else 0.0

    if c24 is not None:
        with ThreadPoolExecutor(max_workers=3) as ex:
            fut_roll = ex.submit(inference_agent_rollups)
            fut_snap = ex.submit(ai_gateway_token_snapshots)
            fut_q = ex.submit(quality_observability)
            rollups = fut_roll.result(timeout=300)
            snap = fut_snap.result(timeout=300)
            qo = fut_q.result(timeout=300)
        gw_rollup = ai_gateway_usage_rollup(24)
        gw_models = (gw_rollup.get("by_model") or []) if not gw_rollup.get("error") else []
        gw_req = int(gw_rollup.get("total_requests") or 0) if not gw_rollup.get("error") else 0
        gw_window = ai_gateway_usage_rollup(wh)
        gw_tokens_window = (
            int(gw_window.get("total_tokens") or 0) if not gw_window.get("error") else None
        )
        count_window: int | None = None
        fqns, _fqnote = inference_fqn_list()
        if fqns and inf.get("time_column"):
            try:
                table_sql = inference_query_table_sql(fqns)
                count_window, _ = count_since_hours(table_sql, str(inf["time_column"]), wh)
            except ValueError:
                count_window = None

        req24 = int(c24) if c24 is not None else 0
        req_src = "inference_table"
        if req24 == 0 and gw_req > 0:
            req24 = gw_req
            req_src = "ai_gateway"

        roll_has_traffic = bool(
            rollups and any(int(r.get("requests_24h") or 0) > 0 for r in rollups),
        )
        n_roll = len(rollups) if rollups else 0
        n_gw = len(gw_models)
        n_f = len(fqns) if fqns else 0
        n_discovered = int(inf.get("tables_discovered_count") or 0) or discovered_payload_table_count()
        # One payload table per AI Gateway route (includes idle tables).
        n_agents = max(n_discovered, n_f, 0) if (n_discovered or n_f) else max(n_gw, n_roll if roll_has_traffic else 0, 0)
        if n_agents == 0:
            n_agents = max(1, min(99, req24 // 500 + 1))

        q_score = quality_score_from_observability(qo)

        return OverviewDTO(
            generated_at=datetime.now(tz=UTC),
            environment=env,
            data_mode="live",
            agents_monitored=n_agents,
            requests_24h=req24,
            requests_24h_source=req_src,
            error_rate_pct=round(err_pct, 3),
            est_monthly_cost_usd=None,
            quality_score_avg=q_score,
            databricks_sql_reachable=True,
            inference_setup_hint=None,
            count_7d=c7 if isinstance(c7, int) else None,
            gateway_tokens_24h=snap.get("total_tokens_24h") if not snap.get("error") else None,
            gateway_tokens_7d=snap.get("total_tokens_7d") if not snap.get("error") else None,
            gateway_usage_error=snap.get("error"),
            window_hours=wh,
            count_window=count_window,
            gateway_tokens_window=gw_tokens_window,
        )

    # SQL works; inference payload tables missing or unreadable — still surface AI Gateway when available.
    snap = ai_gateway_token_snapshots()
    gw_rollup = ai_gateway_usage_rollup(24)
    gw_window = ai_gateway_usage_rollup(wh)
    gw_req = int(gw_rollup.get("total_requests") or 0) if not gw_rollup.get("error") else 0
    gw_models = (gw_rollup.get("by_model") or []) if not gw_rollup.get("error") else []
    n_discovered = int(inf.get("tables_discovered_count") or 0) or discovered_payload_table_count()
    fqns_partial, _ = inference_fqn_list()
    n_agents = max(n_discovered, len(fqns_partial), len(gw_models), 0)
    qo = quality_observability()
    q_score = quality_score_from_observability(qo)
    gw_err_pct = float(qo.get("error_rate_pct") or 0) if not qo.get("error") else 0.0
    return OverviewDTO(
        generated_at=datetime.now(tz=UTC),
        environment=env,
        data_mode="live_partial",
        agents_monitored=n_agents,
        requests_24h=gw_req,
        requests_24h_source="ai_gateway" if gw_req > 0 else "none",
        error_rate_pct=round(gw_err_pct, 3),
        est_monthly_cost_usd=None,
        quality_score_avg=q_score,
        databricks_sql_reachable=True,
        inference_setup_hint=hint
        or "Set AGENTOPS_INFERENCE_SCHEMA, AGENTOPS_INFERENCE_TABLE, or AGENTOPS_INFERENCE_TABLES and restart API. Open /api/v1/inference/diagnostics for details.",
        count_7d=None,
        gateway_tokens_24h=snap.get("total_tokens_24h") if not snap.get("error") else None,
        gateway_tokens_7d=snap.get("total_tokens_7d") if not snap.get("error") else None,
        gateway_usage_error=snap.get("error") or gw_rollup.get("error"),
        window_hours=wh,
        count_window=None,
        gateway_tokens_window=(
            int(gw_window.get("total_tokens") or 0) if not gw_window.get("error") else None
        ),
    )
