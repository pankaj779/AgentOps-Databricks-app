"""Probe system.billing.usage filters (run from backend/: python scripts/probe_billing.py)."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402
from app.databricks.sql_client import sql_connection  # noqa: E402


def run(sql: str, label: str) -> None:
    print(f"\n=== {label} ===")
    try:
        with sql_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
            for r in rows[:12]:
                print(r)
            if len(rows) > 12:
                print(f"... +{len(rows) - 12} more")
            if not rows:
                print("(no rows)")
    except Exception as e:
        print(f"ERROR: {e}")


def main() -> None:
    s = get_settings()
    ws = s.workspace_id.strip()
    print(f"workspace_id={ws} host={s.databricks_host}")
    h = 168
    wpred = f"CAST(workspace_id AS STRING) = '{ws}'" if ws else "1=1"

    run(
        f"""
        SELECT billing_origin_product, usage_type, usage_unit, COUNT(*) AS n,
               CAST(SUM(usage_quantity) AS DOUBLE) AS q
        FROM system.billing.usage
        WHERE {wpred}
          AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS
        GROUP BY 1, 2, 3
        ORDER BY q DESC NULLS LAST
        LIMIT 20
        """,
        "All usage by product/type/unit (current filter: none)",
    )

    run(
        f"""
        SELECT CAST(SUM(usage_quantity) AS DOUBLE) AS q
        FROM system.billing.usage
        WHERE {wpred}
          AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS
          AND usage_type = 'TOKEN'
          AND billing_origin_product = 'MODEL_SERVING'
        """,
        "OLD AgentOps filter (TOKEN + MODEL_SERVING)",
    )

    run(
        f"""
        SELECT CAST(SUM(usage_quantity) AS DOUBLE) AS q
        FROM system.billing.usage
        WHERE {wpred}
          AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS
          AND billing_origin_product = 'MODEL_SERVING'
          AND usage_unit = 'DBU'
        """,
        "Databricks AI Gateway doc filter (MODEL_SERVING + DBU)",
    )

    run(
        f"""
        SELECT CAST(SUM(usage_quantity) AS DOUBLE) AS q
        FROM system.billing.usage
        WHERE {wpred}
          AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS
          AND sku_name LIKE '%SERVERLESS_REAL_TIME_INFERENCE%'
        """,
        "Model serving SKU pattern",
    )

    run(
        """
        SELECT CAST(workspace_id AS STRING) AS wid, billing_origin_product, usage_unit,
               CAST(SUM(usage_quantity) AS DOUBLE) AS q
        FROM system.billing.usage
        WHERE usage_start_time >= current_timestamp() - INTERVAL 720 HOURS
          AND (billing_origin_product IN ('MODEL_SERVING', 'AI_GATEWAY')
               OR sku_name LIKE '%SERVERLESS_REAL_TIME_INFERENCE%')
        GROUP BY 1, 2, 3
        ORDER BY q DESC NULLS LAST
        LIMIT 15
        """,
        "Account-wide MODEL_SERVING/AI_GATEWAY (30d)",
    )

    run(
        f"""
        SELECT billing_origin_product, COUNT(*) AS n
        FROM system.billing.usage
        WHERE {wpred}
          AND usage_start_time >= current_timestamp() - INTERVAL 2160 HOURS
        GROUP BY 1 ORDER BY n DESC LIMIT 20
        """,
        "All products 90d in workspace",
    )

    run(
        """
        SELECT usage_metadata.ai_gateway
        FROM system.billing.usage
        WHERE billing_origin_product = 'MODEL_SERVING'
        LIMIT 1
        """,
        "Sample ai_gateway struct (any MODEL_SERVING row)",
    )

    run(
        f"""
        SELECT COUNT(*) AS n, CAST(SUM(list_cost) AS DOUBLE) AS usd
        FROM system.billing.attributed_usage
        WHERE CAST(workspace_id AS STRING) = '{ws}'
          AND usage_start_time >= current_timestamp() - INTERVAL {h} HOURS
        """,
        "attributed_usage (if populated)",
    )

    run(
        f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(total_tokens), 0) AS tok
        FROM system.ai_gateway.usage
        WHERE CAST(workspace_id AS STRING) = '{ws}'
          AND event_time >= current_timestamp() - INTERVAL {h} HOURS
        """,
        "AI Gateway usage table (same window)",
    )

    run(
        "DESCRIBE TABLE system.billing.usage",
        "usage_metadata columns (grep ai_gateway)",
    )


if __name__ == "__main__":
    main()
