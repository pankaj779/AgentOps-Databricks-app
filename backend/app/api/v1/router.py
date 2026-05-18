"""Versioned JSON API — Databricks-backed when env is configured."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.analytics import (
    ai_gateway_usage_rollup,
    comparison_group_detail,
    cost_summary,
    governance_audit,
    governance_lineage,
    health_slo_summary,
    health_timeseries,
    list_traces,
    mlflow_system_overview,
    quality_observability,
    quality_trend,
    trace_detail,
)
from app.services.agent_catalog import agents_catalog
from app.services.inference import inference_agent_rollups, inference_fqn_list, inference_metrics, resolve_source_table_fqn
from app.services.overview import build_overview
from app.services.benchmark import BenchmarkPromptBody, run_prompt_benchmark
from app.services.replay import replay_targets_public, run_replay

router = APIRouter(tags=["v1"])


class OverviewResponse(BaseModel):
    generated_at: datetime = Field(description="UTC timestamp of this snapshot")
    environment: str = Field(description="local-demo | databricks-sql | …")
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
    gateway_tokens_24h: int | None = Field(
        default=None,
        description="Sum of total_tokens from system.ai_gateway.usage (24h) when query succeeds",
    )
    gateway_tokens_7d: int | None = Field(
        default=None,
        description="Same as gateway_tokens_24h but 7d window",
    )
    window_hours: int = 168
    count_window: int | None = None
    gateway_tokens_window: int | None = None
    gateway_usage_error: str | None = Field(
        default=None,
        description="SQL error when AI Gateway usage snapshot fails (permissions, etc.)",
    )


class AgentSummary(BaseModel):
    id: str
    name: str
    status: str
    rpm: float
    p95_latency_ms: float
    error_rate_pct: float
    source: str = Field(default="demo", description="demo | inference_table | setup | …")
    agent_key: str | None = Field(
        default=None,
        description="Drill-down: inf:<UC FQN> or gw:<AI Gateway model label>",
    )


def _agent_key_for_rollup(r: dict[str, Any], fqns: list[str]) -> str:
    if r.get("group_column") == "_agentops_source_table":
        return f"inf:{r['id']}"
    rid = str(r.get("id") or "")
    rl = rid.lower()
    for f in fqns:
        if f.lower() == rl or f.split(".")[-1].lower() == rl:
            return f"inf:{f}"
    return f"inf:{rid}"


def _analytics_filters(
    agent: str | None,
    source_table: str | None,
    gateway_model: str | None,
) -> tuple[str | None, str | None]:
    """Return (source_table_fqn_fragment, gateway_model) for analytics SQL."""
    if agent and agent.strip():
        a = agent.strip()
        if a.startswith("inf:"):
            return a[4:].strip() or None, None
        if a.startswith("gw:"):
            return None, a[3:].strip() or None
        st = resolve_source_table_fqn(a)
        if st:
            return st, None
        return None, a
    return source_table, gateway_model


def _scope_from_request(
    agent: str | None,
    agents: list[str] | None,
    source_table: str | None,
    gateway_model: str | None,
) -> tuple[list[str], list[str]]:
    """Resolve URL scope to (inference table fqns/keys, gateway model labels)."""
    keys: list[str] = []
    if agents:
        for a in agents:
            if a and str(a).strip():
                k = str(a).strip()
                if k not in keys:
                    keys.append(k)
    elif agent and str(agent).strip():
        keys.append(str(agent).strip())
    infer: list[str] = []
    gw: list[str] = []
    for k in keys:
        st, g = _analytics_filters(k, None, None)
        if st and st not in infer:
            infer.append(st)
        if g and g not in gw:
            gw.append(g)
    if not keys:
        st, g = _analytics_filters(None, source_table, gateway_model)
        if st and st not in infer:
            infer.append(st)
        if g and g not in gw:
            gw.append(g)
    return infer, gw


def _synthetic_agent_rows() -> list[AgentSummary]:
    return [
        AgentSummary(
            id="agent-sales-eu",
            name="Sales Copilot (EU)",
            status="healthy",
            rpm=42.3,
            p95_latency_ms=890,
            error_rate_pct=0.31,
            source="synthetic",
        ),
        AgentSummary(
            id="agent-support-us",
            name="Support Triage (US)",
            status="degraded",
            rpm=118.0,
            p95_latency_ms=2400,
            error_rate_pct=1.85,
            source="synthetic",
        ),
        AgentSummary(
            id="agent-internal-rag",
            name="Internal RAG Assistant",
            status="healthy",
            rpm=15.1,
            p95_latency_ms=1200,
            error_rate_pct=0.12,
            source="synthetic",
        ),
    ]


class ReplayRunBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=256)
    target_ids: list[str] | None = Field(default=None, description="Subset of configured replay target ids")
    track_in_dashboard: bool = Field(
        default=False,
        description="Keep test calls in Agents/Overview lists (gateway may still log either way).",
    )


@router.get("/overview", response_model=OverviewResponse)
def overview(hours: int = Query(168, ge=1, le=24 * 90)) -> OverviewResponse:
    dto = build_overview(window_hours=hours)
    return OverviewResponse(
        generated_at=dto.generated_at,
        environment=dto.environment,
        data_mode=dto.data_mode,
        agents_monitored=dto.agents_monitored,
        requests_24h=dto.requests_24h,
        requests_24h_source=dto.requests_24h_source,
        error_rate_pct=dto.error_rate_pct,
        est_monthly_cost_usd=dto.est_monthly_cost_usd,
        quality_score_avg=dto.quality_score_avg,
        databricks_sql_reachable=dto.databricks_sql_reachable,
        inference_setup_hint=dto.inference_setup_hint,
        count_7d=dto.count_7d,
        gateway_tokens_24h=dto.gateway_tokens_24h,
        gateway_tokens_7d=dto.gateway_tokens_7d,
        gateway_usage_error=dto.gateway_usage_error,
        window_hours=dto.window_hours,
        count_window=dto.count_window,
        gateway_tokens_window=dto.gateway_tokens_window,
    )


@router.get("/inference/diagnostics")
def inference_diagnostics() -> dict[str, Any]:
    """Helps onboard real data: table readable, columns, 24h/7d counts (no row payloads)."""
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured"}
    return inference_metrics()


@router.get("/agents", response_model=list[AgentSummary])
def agents() -> list[AgentSummary]:
    if get_settings().use_demo_metrics:
        return _synthetic_agent_rows()

    dto = build_overview()

    if dto.data_mode == "demo":
        return [
            AgentSummary(
                id="checklist-env",
                name="Add workspace credentials + SQL warehouse to backend/.env",
                status="healthy",
                rpm=0,
                p95_latency_ms=0,
                error_rate_pct=0,
                source="checklist",
            ),
        ]

    if dto.data_mode == "disconnected":
        return [
            AgentSummary(
                id="sql-not-reachable",
                name="Databricks SQL warehouse unreachable — see /api/health",
                status="degraded",
                rpm=0,
                p95_latency_ms=0,
                error_rate_pct=0,
                source="error",
            ),
        ]

    if dto.data_mode == "live_partial":
        hint = (dto.inference_setup_hint or "Open GET /api/v1/inference/diagnostics")[:200]
        return [
            AgentSummary(
                id="inference-wiring",
                name=hint,
                status="degraded",
                rpm=0,
                p95_latency_ms=0,
                error_rate_pct=0,
                source="setup",
            ),
        ]

    if dto.data_mode == "live":
        rollups = inference_agent_rollups()
        fqns, _ = inference_fqn_list()
        gw = ai_gateway_usage_rollup(24)
        gw_models = (gw.get("by_model") or []) if not gw.get("error") else []

        def _rows_from_gateway() -> list[AgentSummary]:
            return [
                AgentSummary(
                    id=str(m.get("model") or "unknown")[:128],
                    name=f"{m.get('model') or 'unknown'} (AI Gateway)",
                    status="healthy",
                    rpm=float(m.get("requests") or 0) / (24.0 * 60.0),
                    p95_latency_ms=0,
                    error_rate_pct=0.0,
                    source="ai_gateway",
                    agent_key=f"gw:{str(m.get('model') or 'unknown')}",
                )
                for m in gw_models
            ]

        roll_has_traffic = bool(
            rollups and any(int(r.get("requests_24h") or 0) > 0 for r in rollups)
        )
        if roll_has_traffic:
            return [
                AgentSummary(
                    id=r["id"],
                    name=f"{r['name']} ({r.get('group_column', 'group')})",
                    status="healthy" if r.get("error_rate_pct", 0) < 2.0 else "degraded",
                    rpm=float(r["rpm"]),
                    p95_latency_ms=float(r.get("p95_latency_ms") or 0),
                    error_rate_pct=float(r.get("error_rate_pct") or 0),
                    source="inference_table",
                    agent_key=_agent_key_for_rollup(r, fqns),
                )
                for r in rollups
            ]
        if gw_models:
            return _rows_from_gateway()
        if rollups:
            return [
                AgentSummary(
                    id=r["id"],
                    name=f"{r['name']} ({r.get('group_column', 'group')})",
                    status="healthy" if r.get("error_rate_pct", 0) < 2.0 else "degraded",
                    rpm=float(r["rpm"]),
                    p95_latency_ms=float(r.get("p95_latency_ms") or 0),
                    error_rate_pct=float(r.get("error_rate_pct") or 0),
                    source="inference_table",
                    agent_key=_agent_key_for_rollup(r, fqns),
                )
                for r in rollups
            ]
        rpm = dto.requests_24h / (24.0 * 60.0) if dto.requests_24h else 0.0
        return [
            AgentSummary(
                id="inference-aggregate",
                name="All traffic (add endpoint/model column to split by agent)",
                status="healthy" if dto.error_rate_pct < 2.0 else "degraded",
                rpm=rpm,
                p95_latency_ms=0,
                error_rate_pct=dto.error_rate_pct,
                source="inference_table",
            ),
        ]

    return _synthetic_agent_rows()


@router.get("/agents/catalog")
def agents_catalog_route() -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"agents": [], "error": "DATABRICKS_TOKEN not configured"}
    return agents_catalog()


# --- Analytics (inference tables + UC system tables) ---


@router.get("/analytics/health/timeseries")
def analytics_health_timeseries(
    hours: int = 168,
    agent: str | None = None,
    agents: list[str] | None = Query(None),
    source_table: str | None = None,
    gateway_model: str | None = None,
) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured", "buckets": []}
    infer, _gw = _scope_from_request(agent, agents, source_table, gateway_model)
    if len(infer) > 1:
        return health_timeseries(hours=hours, source_tables=infer)
    st_single = infer[0] if len(infer) == 1 else None
    return health_timeseries(hours=hours, source_table=st_single)


@router.get("/analytics/health/slo")
def analytics_health_slo(
    p95_target_ms: float = 2000,
    error_budget_pct: float = 1.0,
    agent: str | None = None,
    agents: list[str] | None = Query(None),
    source_table: str | None = None,
    gateway_model: str | None = None,
) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured"}
    infer, _gw = _scope_from_request(agent, agents, source_table, gateway_model)
    if len(infer) > 1:
        return health_slo_summary(
            p95_target_ms=p95_target_ms,
            error_budget_pct=error_budget_pct,
            source_tables=infer,
        )
    st_single = infer[0] if len(infer) == 1 else None
    return health_slo_summary(
        p95_target_ms=p95_target_ms,
        error_budget_pct=error_budget_pct,
        source_table=st_single,
    )


@router.get("/analytics/cost/summary")
def analytics_cost_summary(
    hours: int = 168,
    agent: str | None = None,
    agents: list[str] | None = Query(None),
    source_table: str | None = None,
    gateway_model: str | None = None,
    task: str | None = None,
    tasks: list[str] | None = Query(None),
    request_id: str | None = None,
) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured"}
    infer, gw = _scope_from_request(agent, agents, source_table, gateway_model)
    rid_list: list[str] = []
    if tasks:
        rid_list.extend(str(t).strip() for t in tasks if t and str(t).strip())
    single = (task or request_id or "").strip()
    return cost_summary(
        hours=hours,
        source_tables=infer if infer else None,
        gateway_models=gw if gw else None,
        request_id=single or None,
        request_ids=rid_list if rid_list else None,
    )


@router.get("/analytics/traces")
def analytics_traces(
    limit: int = 40,
    agent: str | None = None,
    agents: list[str] | None = Query(None),
    source_table: str | None = None,
    gateway_model: str | None = None,
) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured", "traces": []}
    infer, gw = _scope_from_request(agent, agents, source_table, gateway_model)
    return list_traces(
        limit=limit,
        source_tables=infer if infer else None,
        gateway_models=gw if gw else None,
    )


@router.get("/analytics/traces/{request_id}")
def analytics_trace_detail(request_id: str) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured"}
    return trace_detail(request_id)


@router.get("/analytics/comparison")
def analytics_comparison(group_id: str) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured", "rows": []}
    return comparison_group_detail(group_id)


@router.post("/benchmark/prompt")
def benchmark_prompt_endpoint(body: BenchmarkPromptBody) -> dict[str, Any]:
    """Send the same chat completion payload to every configured replay target."""
    return run_prompt_benchmark(body)


@router.get("/replay/targets")
def replay_targets() -> dict[str, Any]:
    """Public metadata only — URLs live in env on the server."""
    return replay_targets_public()


@router.post("/replay/run")
def replay_run_endpoint(body: ReplayRunBody) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured", "results": []}
    return run_replay(
        body.request_id,
        body.target_ids,
        track_in_dashboard=body.track_in_dashboard,
    )


@router.get("/analytics/quality/observability")
def analytics_quality_observability() -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured"}
    return quality_observability()


@router.get("/analytics/quality/trend")
def analytics_quality_trend(days: int = 14) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured", "points": []}
    return quality_trend(days=days)


@router.get("/analytics/mlflow/overview")
def analytics_mlflow_overview(limit: int = 20) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured"}
    return mlflow_system_overview(run_limit=limit)


@router.get("/analytics/governance/lineage")
def analytics_governance_lineage(limit: int = 80) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured"}
    return governance_lineage(limit=limit)


@router.get("/analytics/governance/audit")
def analytics_governance_audit(limit: int = 40) -> dict[str, Any]:
    if not get_settings().databricks_token:
        return {"error": "DATABRICKS_TOKEN not configured", "events": []}
    return governance_audit(limit=limit)
