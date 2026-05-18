"""HTTP replay of a logged request against alternate model endpoints (local compare)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import BACKEND_ROOT, get_settings
from app.services.analytics import trace_detail
from app.services.compare_run import (
    apply_tracking_stamps,
    compute_objective_summary,
    cost_estimate_meta,
    execute_all_targets,
    extract_question_from_payload,
    tracking_headers,
)


class ReplayTargetSpec(BaseModel):
    id: str
    label: str
    url: str
    #: When set, overrides request JSON ``model`` (required when every target shares one gateway URL).
    model: str | None = None
    timeout_sec: float = Field(default=120.0, ge=1.0, le=600.0)
    headers: dict[str, str] = Field(default_factory=dict)


def _normalize_bearer_token(raw: str) -> str:
    t = raw.strip()
    if t.lower().startswith("bearer "):
        return t[7:].strip()
    return t


def _ai_gateway_token() -> str:
    s = get_settings()
    tok = _normalize_bearer_token(s.databricks_ai_gateway_token) or _normalize_bearer_token(
        s.databricks_token,
    )
    return tok


def _merge_auth_headers(headers: dict[str, str], *, track_in_dashboard: bool) -> dict[str, str]:
    """Attach PAT for Databricks AI Gateway HTTP calls (replay targets omit secrets)."""
    out = dict(headers or {})
    out.setdefault("Content-Type", "application/json")
    tok = _ai_gateway_token()
    if tok:
        out["Authorization"] = f"Bearer {tok}"
    out.update(tracking_headers(track_in_dashboard))
    return out


def _payload_for_target(body: dict[str, Any], target: ReplayTargetSpec) -> dict[str, Any]:
    payload = json.loads(json.dumps(body))
    if target.model and str(target.model).strip():
        payload["model"] = str(target.model).strip()
    return payload


def _replay_target_file_candidates() -> list[Path]:
    """All plausible locations for replay_targets.json (cwd, backend/, backend/app/)."""
    s = get_settings()
    name = (s.replay_targets_file or "replay_targets.json").strip() or "replay_targets.json"
    here = Path(__file__).resolve()
    roots: list[Path] = [
        BACKEND_ROOT,
        BACKEND_ROOT.parent,
        here.parents[2] if len(here.parents) > 2 else BACKEND_ROOT,
        here.parents[1] if len(here.parents) > 1 else BACKEND_ROOT,
        Path.cwd(),
        Path.cwd() / "backend",
        Path.cwd() / "backend" / "app",
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        try:
            p = (root / name).resolve()
        except OSError:
            continue
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _replay_targets_json_raw() -> tuple[str, str, list[str]]:
    """Return (raw_json, primary_source_label, notes). File is tried first; env JSON used if file missing/empty."""
    s = get_settings()
    notes: list[str] = []
    for p in _replay_target_file_candidates():
        if not p.is_file():
            notes.append(f"missing_file:{p}")
            continue
        try:
            txt = p.read_text(encoding="utf-8").strip()
            if txt:
                return txt, str(p), notes
            notes.append(f"empty_file:{p}")
        except OSError as e:
            notes.append(f"read_error:{p}:{e}")
    raw = (s.replay_targets_json or "").strip()
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        raw = raw[1:-1].strip()
    if raw:
        return raw, "AGENTOPS_REPLAY_TARGETS_JSON", notes
    default_json = BACKEND_ROOT / "replay_targets.json"
    if default_json.is_file():
        try:
            txt = default_json.read_text(encoding="utf-8").strip()
            if txt:
                notes.append("auto:replay_targets.json (no env var set)")
                return txt, str(default_json), notes
        except OSError as e:
            notes.append(f"auto_read_error:{e}")
    return "", "none", notes


def _parse_replay_targets_list(raw: str) -> tuple[list[ReplayTargetSpec], str | None]:
    if not raw:
        return [], None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], str(e).strip()[:400]
    if not isinstance(data, list):
        return [], "replay targets JSON must be an array [...]"
    out: list[ReplayTargetSpec] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ReplayTargetSpec(**item))
        except Exception:
            continue
    if not out and raw.strip().startswith("["):
        return [], "array had no valid items (each needs id, label, url)"
    return out, None


def load_replay_targets() -> list[ReplayTargetSpec]:
    raw, _src, _notes = _replay_targets_json_raw()
    targets, _err = _parse_replay_targets_list(raw)
    return targets


def replay_targets_public() -> dict[str, Any]:
    raw, src, load_notes = _replay_targets_json_raw()
    targets, parse_err = _parse_replay_targets_list(raw)
    s = get_settings()
    sql_tok = _normalize_bearer_token(s.databricks_token)
    gw_tok = _ai_gateway_token()
    out: dict[str, Any] = {
        "targets": [
            {"id": t.id, "label": t.label, "model": t.model, "url": t.url}
            for t in targets
        ],
        "auth": {
            "sql_token_configured": bool(sql_tok),
            "gateway_token_configured": bool(gw_tok),
            "uses_separate_gateway_token": bool(_normalize_bearer_token(s.databricks_ai_gateway_token)),
            "bearer_sent_on_replay": bool(gw_tok),
            "hint": (
                "SQL uses DATABRICKS_TOKEN; replay/benchmark uses DATABRICKS_AI_GATEWAY_TOKEN "
                "if set, else DATABRICKS_TOKEN. Gateway PAT needs scope ai-gateway; SQL PAT needs sql "
                "(and unity-catalog for UC tables / system.billing for Cost)."
            )
            if gw_tok
            else "Set DATABRICKS_TOKEN or DATABRICKS_AI_GATEWAY_TOKEN in backend/.env",
        },
    }
    if not targets:
        diag: dict[str, Any] = {"configured_from": src, "raw_length": len(raw), "load_notes": load_notes}
        if parse_err:
            diag["parse_error"] = parse_err
        if not raw.strip():
            diag["hint"] = (
                "Set AGENTOPS_REPLAY_TARGETS_JSON (single-line JSON) and/or AGENTOPS_REPLAY_TARGETS_FILE "
                "(path under backend/, e.g. replay_targets.json — copy from replay_targets.example.json)."
            )
        elif parse_err:
            diag["hint"] = (
                "Fix JSON syntax. On Windows use a file: create backend/replay_targets.json and set "
                "AGENTOPS_REPLAY_TARGETS_FILE=replay_targets.json"
            )
        else:
            diag["hint"] = "Parsed JSON but no valid targets (each object needs id, label, url)."
        out["diagnostics"] = diag
    return out


def _run_targets(
    *,
    base_payload: dict[str, Any],
    targets: list[ReplayTargetSpec],
    track_in_dashboard: bool,
) -> list[dict[str, Any]]:
    return execute_all_targets(
        targets,
        base_payload=base_payload,
        track_in_dashboard=track_in_dashboard,
        merge_auth_headers=_merge_auth_headers,
        payload_for_target=_payload_for_target,
    )


def run_replay(
    request_id: str,
    target_ids: list[str] | None = None,
    *,
    track_in_dashboard: bool = False,
) -> dict[str, Any]:
    """POST logged `request_json` to each configured target; return timings + parsed usage."""
    detail = trace_detail(request_id)
    if detail.get("error"):
        return {"error": detail["error"], "request_id": request_id, "results": []}

    body = detail.get("request_json")
    if body is None:
        rec = detail.get("record") or {}
        req_raw = rec.get("request")
        if isinstance(req_raw, str):
            try:
                body = json.loads(req_raw)
            except json.JSONDecodeError:
                body = None
    if body is None:
        return {"error": "no_request_json_for_replay", "request_id": request_id, "results": []}

    targets = load_replay_targets()
    if target_ids:
        want = set(target_ids)
        targets = [t for t in targets if t.id in want]
    if not targets:
        return {"error": "no_replay_targets_configured", "request_id": request_id, "results": []}

    base = body if isinstance(body, dict) else {}
    question = extract_question_from_payload(base)
    results = _run_targets(
        base_payload=base,
        targets=targets,
        track_in_dashboard=track_in_dashboard,
    )
    cost_meta = cost_estimate_meta()
    return {
        "request_id": request_id,
        "results": results,
        "question": question,
        "track_in_dashboard": track_in_dashboard,
        "objective_summary": compute_objective_summary(results),
        "cost_estimate": cost_meta,
        "tracking_note": (
            "Databricks may still log inference payloads and bill tokens. "
            "AgentOps hides tests from dashboard lists when Track is off and "
            "AGENTOPS_EXCLUDE_TEST_REQUESTS=true."
        ),
        "error": None,
    }
