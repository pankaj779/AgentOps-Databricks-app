"""Merged view of AI Gateway routes (+ linked inference tables) for drill-down UIs."""

from __future__ import annotations

from typing import Any

from app.services.agent_unify import unified_agents_catalog


def agents_catalog() -> dict[str, Any]:
    """Stable keys: gw:<route slug> (primary). Inference FQN linked on the same row when discoverable."""
    return unified_agents_catalog()
