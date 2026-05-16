"""Unity Catalog three-part name helpers for Spark / Databricks SQL."""

from __future__ import annotations

import re

# Three dot-separated segments; allow hyphens (common in AI Gateway *_payload table names).
_SEG = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_\-]{0,255}$")


def validate_fqn(fqn: str) -> bool:
    f = fqn.strip()
    parts = f.split(".")
    if len(parts) != 3:
        return False
    return all(p and bool(_SEG.match(p)) for p in parts)


def validate_schema_fqn(s: str) -> bool:
    """Two-part Unity Catalog name: catalog.schema (for auto table discovery)."""
    f = s.strip()
    parts = f.split(".")
    if len(parts) != 2:
        return False
    return all(p and bool(_SEG.match(p)) for p in parts)


def quote_fqn(fqn: str) -> str:
    f = fqn.strip()
    if not validate_fqn(f):
        raise ValueError("Invalid three-part name (expected catalog.schema.table).")
    a, b, c = f.split(".")
    return f"`{a}`.`{b}`.`{c}`"


def quote_schema_fqn(schema_two: str) -> str:
    s = schema_two.strip()
    if not validate_schema_fqn(s):
        raise ValueError("Invalid two-part name (expected catalog.schema).")
    a, b = s.split(".", 1)
    return f"`{a}`.`{b}`"
