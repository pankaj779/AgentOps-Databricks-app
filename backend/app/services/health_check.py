"""One-click workspace diagnostics for the UI."""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.services.agent_unify import unified_agents_catalog
from app.services.analytics import billing_model_serving_cost
from app.services.gateway_aliases import load_gateway_aliases
from app.services.inference import inference_fqn_list, inference_metrics
from app.services.replay import load_replay_targets


def run_workspace_health_check() -> dict[str, Any]:
    s = get_settings()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, fix: str | None = None) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "fix_hint": fix})

    add(
        "DATABRICKS_TOKEN",
        bool(s.databricks_token.strip()),
        "SQL PAT configured" if s.databricks_token.strip() else "Missing PAT",
        "Set DATABRICKS_TOKEN in backend/.env",
    )
    add(
        "DATABRICKS_HTTP_PATH",
        bool(s.databricks_http_path.strip()),
        "Warehouse path set" if s.databricks_http_path.strip() else "Missing warehouse",
        "Set DATABRICKS_HTTP_PATH",
    )
    add(
        "WORKSPACE_ID",
        bool(s.workspace_id.strip()),
        f"workspace_id={s.workspace_id}" if s.workspace_id.strip() else "Unset (billing scope weak)",
        "Set DATABRICKS_WORKSPACE_ID for billing",
    )

    inf = inference_metrics()
    add(
        "Inference payload tables",
        bool(inf.get("count_24h") is not None or inf.get("time_column")),
        inf.get("describe_error") or inf.get("count_error") or f"OK · time_col={inf.get('time_column')}",
        "Set AGENTOPS_INFERENCE_SCHEMA=agentops.agent_logs",
    )

    fqns, note = inference_fqn_list()
    add(
        "Discovered tables",
        len(fqns) >= 1,
        f"{len(fqns)} table(s) · {note or 'ok'}",
        None,
    )

    cat = unified_agents_catalog()
    add(
        "Agent catalog",
        len(cat.get("agents") or []) > 0,
        f"{len(cat.get('agents') or [])} agent route(s)",
        None,
    )

    targets = load_replay_targets()
    add(
        "Replay targets",
        len(targets) > 0,
        f"{len(targets)} target(s) in replay_targets.json",
        "Fix backend/replay_targets.json array",
    )

    bill = billing_model_serving_cost(24)
    add(
        "Billing usage",
        not bill.get("error") and (bill.get("total_dbu") is not None),
        bill.get("error") or f"DBU={bill.get('total_dbu')} · ${bill.get('total_list_usd')}",
        "Grant SELECT on system.billing.usage + list_prices",
    )

    aliases = load_gateway_aliases()
    add(
        "Gateway aliases",
        len(aliases) > 0,
        f"{len(aliases)} alias key(s) loaded",
        "Edit backend/gateway_aliases.json",
    )

    ok_count = sum(1 for c in checks if c["ok"])
    return {
        "checks": checks,
        "passed": ok_count,
        "total": len(checks),
        "all_ok": ok_count == len(checks),
    }
