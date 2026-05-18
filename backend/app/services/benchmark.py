"""Fan-out the same chat prompt to each configured replay target (OpenAI-style JSON)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.compare_run import (
    compute_objective_summary,
    cost_estimate_meta,
    execute_all_targets,
    extract_question_from_payload,
)
from app.services.replay import (
    _merge_auth_headers,
    _payload_for_target,
    load_replay_targets,
)


class BenchmarkPromptBody(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    max_tokens: int = Field(default=256, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    target_ids: list[str] | None = Field(default=None, description="Subset of replay target ids")
    track_in_dashboard: bool = Field(
        default=False,
        description="When true, test rows remain visible in Agents/Overview trace lists.",
    )


def run_prompt_benchmark(body: BenchmarkPromptBody) -> dict[str, Any]:
    s = get_settings()
    if not s.benchmark_enabled:
        return {
            "error": "benchmark_disabled",
            "hint": "Set AGENTOPS_BENCHMARK_ENABLED=true after configuring AGENTOPS_REPLAY_TARGETS_JSON.",
            "results": [],
        }
    targets = load_replay_targets()
    if body.target_ids:
        want = set(body.target_ids)
        targets = [t for t in targets if t.id in want]
    if len(targets) > s.benchmark_max_targets:
        targets = targets[: s.benchmark_max_targets]
    if not targets:
        return {
            "error": "no_replay_targets",
            "hint": "Add URLs in AGENTOPS_REPLAY_TARGETS_JSON (same schema as replay).",
            "results": [],
        }

    base_payload: dict[str, Any] = {
        "model": "agentops-benchmark",
        "messages": body.messages,
        "max_tokens": body.max_tokens,
        "temperature": body.temperature,
    }
    question = extract_question_from_payload(base_payload)
    results = execute_all_targets(
        targets,
        base_payload=base_payload,
        track_in_dashboard=body.track_in_dashboard,
        merge_auth_headers=_merge_auth_headers,
        payload_for_target=_payload_for_target,
    )
    cost_meta = cost_estimate_meta()

    return {
        "error": None,
        "results": results,
        "question": question,
        "track_in_dashboard": body.track_in_dashboard,
        "objective_summary": compute_objective_summary(results),
        "cost_estimate": cost_meta,
        "note": "Same JSON body sent to each target; compare usage, cost estimate, latency, and answers.",
        "tracking_note": (
            "Databricks may still log inference payloads and bill tokens. "
            "AgentOps hides tests from dashboard lists when Track is off and "
            "AGENTOPS_EXCLUDE_TEST_REQUESTS=true."
        ),
    }
