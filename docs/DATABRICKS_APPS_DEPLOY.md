# Deploy AgentOps to Databricks Apps

## Prerequisites

- Databricks CLI configured for your workspace
- Service principal or user with SQL warehouse + UC access
- Secrets scope for `DATABRICKS_TOKEN`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_WORKSPACE_ID`

## Build frontend

```bash
cd frontend
npm ci
npm run build
```

Copy `frontend/dist` to `backend/static` (or serve via FastAPI static mount).

## Environment (production)

Set in the app secret scope (never commit):

- `DATABRICKS_HOST`
- `DATABRICKS_TOKEN`
- `DATABRICKS_HTTP_PATH`
- `DATABRICKS_WORKSPACE_ID`
- `AGENTOPS_INFERENCE_SCHEMA=agentops.agent_logs`
- `AGENTOPS_EXCLUDE_TEST_REQUESTS=true`
- `AGENTOPS_BENCHMARK_ENABLED=true`
- `AGENTOPS_REPLAY_TARGETS_FILE=replay_targets.json`

## Run locally (dev)

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

```bash
cd frontend
npm run dev
```

## Health check

UI: **Health check** button in the top status bar, or:

```bash
curl http://127.0.0.1:8080/api/v1/health-check/workspace
```

## User settings (SQLite)

Preferences (hide tests toggle, default compare prompt) persist in `backend/agentops_app_config.db`.
For team-wide settings, migrate this store to Lakebase when ready.

## Demo notes (telemetry vs MLflow vs chat model)

- **This AgentOps app** reads **Unity Catalog Inference Tables**, **AI Gateway usage**, and billing tables. It **does not** call an LLM for the dashboard.
- **MLflow UC system tables** (`system.mlflow.*`) populate when workloads use **MLflow Tracking** (e.g. `mlflow.start_run`). Routes that only use **AI Gateway / Inference Tables** can show experiments but **0 runs** until something logs runs there.
- **Caller / requester** in traces is whatever Databricks records (often an **OAuth client** or **service principal UUID**). That is normal; it is **not** always a person's name.
- The **Databricks Apps agent template** (separate codebase) chooses the model via its **`model=`** argument and Gateway routing (e.g. your `agentops_test` endpoint fronts a specific hosted model — check Gateway config and **`system.ai_gateway.usage.endpoint_name`** for what actually ran).

