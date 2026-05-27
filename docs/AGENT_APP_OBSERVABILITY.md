# Databricks Apps agent — observability (tokens, cost, caller)

AgentOps correlates three signals:

1. **Inference Tables** (`agent_logs` payloads) — request/response bodies and an inference `request_id`.
2. **`system.ai_gateway.usage`** — authoritative token counts keyed by **Gateway `request_id`** (often **not** the same id as the inference row for Apps / Agents SDK).
3. **`system.billing.usage`** — list-price DBUs (best-effort; naming may not match pinned filters).

## Why tokens/cost looked empty from the agent app

- Your **Databricks App** usually calls the model as an **OAuth principal** (service principal / client id). That identity is what Databricks logs as **`requester`** — a **UUID**, not a person’s display name.
- The **inference `request_id`** in UC may not equal **`system.ai_gateway.usage.request_id`**, so a naïve join misses until AgentOps applies **alternate id + time/destination + completion `usage`** fallbacks.

## What to implement in the agent (recommended)

1. **Route chat through Unity AI Gateway** using `AsyncDatabricksOpenAI(use_ai_gateway=True)` (or equivalent) and a **Gateway endpoint name** you recognize in Cost / Quality filters.
2. **Keep completion `usage` in the response body** (OpenAI-style `"usage": { "prompt_tokens", "completion_tokens", "total_tokens" }`). If Gateway or the client strips `usage`, AgentOps can only estimate from **char/4 proxies** unless the Gateway system table row correlates.
3. **Optional caller context** — Databricks keeps logging **`requester`** as the **Apps OAuth / service principal**, not an end-user. To show **who typed the prompt** in AgentOps, do one or both:
   - Put the authenticated user id or email on the completion call as **`user`** or under **`metadata`** (e.g. `metadata.display_name`, `metadata.email`) in the logged OpenAI-style request JSON — AgentOps maps that to **`request_actor`** / journey “caller” when present.
   - Or log **`user_email` / `user_name`** (or similar) in inference columns your workspace already exposes, if your ingestion copies them into the inference row.

## What you need from the workspace

- **Latest AgentOps backend** deployed so metering resolution (alternate ids, heuristic match, nested `usage`) runs next to Databricks SQL.
- SQL identity can read **`system.ai_gateway.usage`** and **`system.billing.usage`** for the workspace/account per your entitlements.
