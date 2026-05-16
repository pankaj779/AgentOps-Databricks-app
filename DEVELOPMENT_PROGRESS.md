# AgentOps Dashboard — Development Progress

This file is the **single source of truth** for scope, status, and decisions. It should be **updated whenever** major work lands (features, integrations, deployment). The assistant uses it to preserve context across sessions.

---

## End goal (accelerator)

Deliver a **Databricks-native AgentOps Dashboard** (partner accelerator) that is a credible **“single pane of glass”** for production AI agents:

| Panel | Purpose | Primary data sources (target) |
|--------|---------|----------------------------------|
| **1. Agent health** | Latency, errors, throughput, availability | Inference Tables (via SQL/Delta), AI Gateway logs |
| **2. Cost & tokens** | Spend attribution, burn rate, projections | Inference Tables + **system.billing** / usage system tables |
| **3. Quality & safety** | Groundedness, relevance, safety trends; drill into traces | MLflow Agent Evaluation + MLflow Tracing |
| **4. Governance & audit** | What data agents touched, lineage, compliance-oriented view | Unity Catalog (lineage, permissions, audit) |

**Platform choices (locked for v1 of the codebase):**

- **UI:** React 19 + Vite + TypeScript + Tailwind CSS v4 — full control over layout, branding, and accessibility (vs Streamlit for this use case).
- **API:** FastAPI — aligns with Databricks Apps cookbook patterns, easy Lakebase/UC/SQL integration in Python.
- **Deploy target:** Databricks Apps (FastAPI process serving API + built static UI, or dev: split Vite + API with proxy).

**Success criteria for “done” (accelerator / demo-ready):**

1. Runs locally end-to-end (UI → API → stub or real Databricks reads).
2. Deployable to Databricks Apps with documented `app.yaml` / bundle steps.
3. Four panels populated with **real** or **semi-real** data from a workspace you control (minimum: inference logging + one UC entity; stretch: MLflow eval jobs + system tables).
4. Lakebase used for **app-owned** data (config, thresholds, alert state, cached aggregates) per explainer.
5. Clear story for **governance** (who can see PII prompts, audit narrative) even if some widgets are MVP.

---

## What exists in the repo (created)

| Area | Status | Notes |
|------|--------|--------|
| `agentops-explainer.html` | Done | Product/architecture narrative (reference only). |
| `frontend/` | **In progress** | Light/dark theme; Overview **setup card**, **Refresh**, **Run inference diagnostics**, 7d KPI when live. |
| `backend/` | **In progress** | `GET /api/v1/inference/diagnostics`; inference metrics (24h/7d, error %); **agent rollups** by model/endpoint; `AGENTOPS_INFERENCE_TIME_COLUMN`. |
| Databricks connectivity | **Started** | Env-driven PAT + warehouse HTTP path. **Never commit secrets** — use `backend/.env` (gitignored). |
| Lakebase schema / migrations | **Not started** | |
| MLflow / eval pipelines | **Not started** | |
| Databricks Apps bundle (`app.yaml`, CI) | **Not started** | |
| Automated tests (API + E2E) | **Not started** | |

---

## What still needs to be created (backlog)

High level — order is suggested, not rigid:

1. **Workspace wiring** — Databricks SDK or SQL connector; config via env / OIDC in Apps.
2. **Inference Tables reader** — parameterized table FQN, time-window queries, aggregates for health + cost.
3. **System tables / billing** — token and list-price or account-specific cost model (confirm with your admin).
4. **MLflow** — trace fetch by request id; evaluation results store or API (design depends on your MLflow setup).
5. **Unity Catalog** — table lineage / permissions surface for governance panel (scope MVP vs “full lineage graph”).
6. **Lakebase** — SQLAlchemy/asyncpg or psycopg; tables: `dashboard_config`, `alert_rules`, `alert_events`, `metric_cache`.
7. **Databricks Asset Bundle** — deploy app, secrets, permissions.
8. **Testing** — pytest for API; Playwright or Cypress for UI smoke; optional integration tests against a dev workspace.

---

## Information needed from you (to go beyond mocks)

Please provide when you can (does not all need to be day one):

1. **Databricks workspace** — cloud (AWS/Azure/GCP) and whether you use **Unity Catalog** everywhere for the demo data.
2. **Authentication pattern** — personal dev token for local only vs **service principal** + OAuth (preferred for Apps).
3. **Inference logging** — catalog.schema table name(s) for inference payloads, or confirmation you will create them via AI Gateway; sample **non-PII** row shape if possible.
4. **Lakebase** — connection string format you use (host, port, database, SSL); whether extensions (e.g. pgvector) are required for v1.
5. **Cost model** — OK to show **relative** token counts only, or do you need **USD** (needs pricing agreement / export from billing).
6. **MLflow** — tracking URI / workspace behavior; whether Agent Evaluation jobs already exist or we define them.
7. **Compliance** — any industry constraint (HIPAA, etc.) affecting what we render from prompts/responses.

---

## Connection: what you typically provide (and what that unlocks)

**You do *not* need three unrelated “URLs” in every setup.** What we usually need:

| Input | Why |
|--------|-----|
| **Workspace URL** | e.g. `https://<deployment>.cloud.databricks.com` (or Azure/GCP equivalent). Base for REST + SQL over HTTP. |
| **Auth** | **PAT** is enough for *early local dev* if you accept the risk (short-lived, user-bound). For production / Databricks Apps, prefer **OAuth / service principal** (no long-lived PAT in the app). |
| **SQL execution path** | Most often: a **SQL Warehouse ID** (serverless or classic) *or* a cluster ID used to run SQL. The “SQL URL” people mention is often the **warehouse JDBC/ODBC host + path**, but our Python stack usually talks to Databricks via **SDK / SQL connector** using **host + token + warehouse HTTP path**, not a hand-copied JDBC string (unless you prefer that). |

**Inference / system / MLflow / UC — can PAT + host + SQL access get “everything”?**

- **Inference tables:** Yes *if* AI Gateway (or equivalent) is actually writing logs, you give us the **three-part name** (`catalog.schema.table`), and your identity **SELECT**s on it. Schema varies by product version — we map columns once we see a sample or docs for your table.
- **System tables (e.g. billing / usage):** Often **yes in principle**, but many workspaces **restrict** `system.*` to admins. If your PAT cannot read those tables, we cannot show account-level billing from system tables until **an admin grants** access or you use another approved cost source.
- **MLflow traces / Agent Evaluation:** **Partly tables, partly APIs.** Traces and eval results may live in Delta tables *and* are accessed via **MLflow Tracking / Evaluation APIs** or workspace REST. PAT/SP with the right permissions is usually enough **if** experiments and eval jobs are configured; we do not magically discover experiment IDs without you pointing us at them (or we add a small “settings in Lakebase” config).
- **Unity Catalog (lineage, permissions, audit):** **Yes with grants** — UC is ready in your account only if the app identity has rights to the **Unity Catalog APIs / information schema** / lineage features you want to demo.

**Lakebase — who creates it?**

- **The Lakebase *instance* (Postgres-compatible endpoint in Databricks):** you (or your admin) **provision in the Databricks UI / APIs** — we do not create the Lakebase service from scratch inside this repo.
- **The app’s *tables* (config, alerts, cache):** **our application can create them** via migrations/SQL the first time it connects, once you give a database/user with **CREATE** permission (exact approach TBD with your Lakebase access model).

---

## How to run locally (after scaffold)

**Terminal A — API**

```powershell
Set-Location "c:\Users\Pankaj\My_documents\AgentOps_Databricks\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
```

If `uvicorn` or `pip` **.exe** is blocked by Windows Application Control, keep using **`python -m pip`** and **`python -m uvicorn`** (runs through `python.exe`, which is usually allowed).

**Port `8000` on Windows:** if you see `WinError 10013` (socket access forbidden), another process may be using the port or it may fall in an **excluded port range** (Hyper-V / WSL). This repo defaults the API to **`8080`** and the Vite proxy reads `frontend/.env.development` → `VITE_API_PORT`. If `8080` also fails, pick another free port (e.g. `8765`) and set the same value in `VITE_API_PORT` and `uvicorn --port`.

**Terminal B — UI**

```powershell
Set-Location "c:\Users\Pankaj\My_documents\AgentOps_Databricks\frontend"
npm run dev
```

Open the URL Vite prints (typically `http://127.0.0.1:5173`). The UI calls `/api/...` which Vite proxies to the API port in `frontend/.env.development` (default **8080**).

---

## Phase plan — real data in a few hours (task order)

Work in this order with the running app (API **8080** + `npm run dev`). **Do not** enable `AGENTOPS_USE_DEMO_METRICS` unless you explicitly want fake numbers.

| Step | Task | Done when |
|------|------|-----------|
| **1** | Workspace: SQL warehouse + PAT in `backend/.env` | TopBar shows **Databricks SQL** green; `/api/health` → `sql_reachable: true` |
| **2** | Find or create **inference logging** (Model Serving / AI Gateway / agent) so requests persist to a **UC Delta** table | You have `catalog.schema.table` |
| **3** | Set `AGENTOPS_INFERENCE_TABLE` (and restart API) | Overview shows non-zero **Requests (24h)** or the setup card lists column names |
| **4** | If counts stay 0: click **Run inference diagnostics** on Overview (or `GET /api/v1/inference/diagnostics`) | JSON shows `columns_sample`, `time_column`, `count_24h` |
| **5** | If `time_column` is null: set `AGENTOPS_INFERENCE_TIME_COLUMN` to the right column | `count_24h` appears in diagnostics |
| **6** | Generate traffic (invoke model endpoint several times), **Refresh data** | **Agents & models** table fills from `model_name` / `endpoint_name` / etc. when present |
| **7** | Lakebase (later): provision instance, then we add connection string for app config/alerts | Separate milestone — not required for step 1–6 |

There are **no default “sample agents”** in Databricks with prepopulated production logs; traffic comes from **your** endpoints and logging settings.

---

## Beginner path: you have no agent yet (start here)

### Does this plan make sense?

**Yes.** The usual path is: **(1)** turn on a **small model workload** in Databricks, **(2)** enable **inference logging** so each call writes a row to a **Unity Catalog Delta table**, **(3)** point **AgentOps** at that table. You do **not** need a fancy “agent framework” on day one.

### The three places people confuse

| What | What it is | Where you look | Used by AgentOps (today) |
|------|-------------|----------------|---------------------------|
| **Inference table** | Delta table with **request/response** (or payload) rows from **serving / AI Gateway** | **Catalog Explorer** → catalog/schema you chose when enabling logging; or docs link from the **Serving** endpoint | **Primary** — set `AGENTOPS_INFERENCE_TABLE` |
| **MLflow** | **Experiments**, runs, **traces**, models — different product surface | Left sidebar **Machine Learning** / **Experiments**, or **Models** | **Later** — Quality & traces panel |
| **System tables** | Workspace/account **billing**, usage, audit under **`system`** catalog | SQL Editor / docs: `system.*` (often needs admin grants) | **Later** — Cost panel |

### Simplest first project: **Model Serving** + **inference tables** (not “build an agent” yet)

Use a **serving endpoint** (many workspaces let you deploy a small **open model** or a **tiny sklearn** model). Enable **inference tables** (often under **AI Gateway** or endpoint **Edit** → inference / monitoring). That creates or fills a **UC Delta** table when people **call the endpoint**.

**UI flow (names vary slightly by cloud: AWS / Azure / GCP):**

1. In Databricks, open **Serving** (or **Mosaic AI** → **Serving** / **Model Serving**).
2. **Create serving endpoint** — pick something small (e.g. a **Feature and Inference** tutorial model, or “get started” sample if your workspace offers it).
3. In the endpoint settings, find **AI Gateway**, **Monitoring**, or **Inference tables** (wording depends on version). **Enable** logging to a **catalog** and **schema** you can write to (e.g. `main` / `your_schema` or a dev catalog).
4. Save. Note the **endpoint name** and optional **table name** shown in the UI or docs (sometimes the table is **auto-created** with a predictable pattern — check [Inference tables](https://docs.databricks.com/aws/en/machine-learning/model-serving/inference-tables) and [AI Gateway inference tables](https://docs.databricks.com/aws/en/ai-gateway/inference-tables) for your region).
5. **Send a few requests**: use the **endpoint’s “Query”** UI, or **REST** from Postman, or a **notebook** `invoke` example. Wait a few minutes (**log delivery can lag**).
6. Open **Catalog Explorer** → your **catalog** → **schema** → look for a **new Delta table** (name tied to logging config). Copy **`catalog.schema.table`** into `backend/.env` as `AGENTOPS_INFERENCE_TABLE`.
7. Restart AgentOps API → Overview → **Run inference diagnostics** → confirm **`count_24h`** or fix **`AGENTOPS_INFERENCE_TIME_COLUMN`** using `columns_sample`.

If your workspace uses **Unity AI Gateway** explicitly, the same idea applies: enable **inference tables** for the gateway route / endpoint and pick UC catalog + schema — see official docs above.

### When you graduate to a real “agent”

A **production agent** is usually: orchestration (LLM + tools) **deployed** as an app or job, still often backed by **serving** and/or **gateway** with logging. Start with **serving + inference table** first; add **MLflow Tracing** and **Agent Evaluation** when you have a notebook or app you can wrap.

### Lakebase — connect our app (after inference works)

Do **not** block on Lakebase for your first charts. Lakebase is for **this app’s Postgres data** (configs, alerts, cache).

1. **Provision Lakebase** in your account (admin / Databricks **Data** / database product UI — exact placement varies).
2. Get **host, port, database, user, password**, SSL mode.
3. We add **`LAKEBASE_URL`** (or discrete env vars) + SQLAlchemy migrations in a **follow-up task** — not required to prove inference KPIs.

---

## Why you see zeros (and how to get charts)

| Symptom | Meaning | What to do |
|--------|---------|------------|
| **Databricks SQL green, KPIs all 0** | **Live partial** — warehouse works, but no **inference table** (or COUNT query did not find a time column). | Set **`AGENTOPS_INFERENCE_TABLE=catalog.schema.table`** in `backend/.env` to the Delta table where **AI Gateway / serving / agents** write payloads. Then restart the API. |
| **Want sample KPIs while wiring** | Optional. | Set **`AGENTOPS_USE_DEMO_METRICS=true`** in `backend/.env`. Overview shows **synthetic** demo numbers; TopBar still reflects real SQL when configured. Turn off when you want only real aggregates. |

### End-to-end checklist (real agent telemetry)

1. **Something that generates traffic** — Databricks does **not** ship “default production agents” with pre-filled logs. You need **at least one** of: a **Model Serving** endpoint, **AI Gateway**-routed calls, Mosaic / custom **agent code**, or notebooks hitting models — with **inference logging** enabled so rows land in Unity Catalog (Delta).
2. **A UC table FQN** — In Catalog Explorer or SQL, find the **inference** or **payload** table (name depends on how you enabled logging). Put it in **`AGENTOPS_INFERENCE_TABLE`**.
3. **Schema** — The app tries common timestamp column names (`timestamp`, `request_time`, `created_at`, …). If your table uses another name, tell us or we extend the config.
4. **Lakebase (later)** — You (admin) **create the Lakebase** instance in Databricks; this app will add **app tables** (alerts, config, cache) via migrations once we wire `DATABASE_URL` / Lakebase connection. It does **not** replace inference tables for agent metrics.
5. **Cost / MLflow / UC panels** — Need **system** table access (often admin), **MLflow** experiments/evals, and **UC** lineage APIs — we build these incrementally after inference is flowing.

---

## Changelog (maintain manually or via commits)

| Date | Change |
|------|--------|
| 2026-04-18 | Initial progress file; stack decision (React+Vite+TS+Tailwind, FastAPI); repo scaffold with mock API + UI shell; `npm run build` and backend import verified locally. |
| 2026-04-18 | Documented connection expectations (host, auth, warehouse/SQL path), Lakebase provisioning vs app migrations, and limits of PAT-only access to system tables / MLflow. |
| 2026-04-18 | **Security:** user pasted a PAT in chat — must be **rotated**. Implemented env-based Databricks SQL probe + UI status; added optional inference table 24h count. |
| 2026-04-20 | Light/dark theme toggle; **`AGENTOPS_USE_DEMO_METRICS`**; guide for zeros → inference table + Lakebase/agents expectations. |
| 2026-04-21 | Real-data inference: diagnostics endpoint, time-column override, 7d counts, error %, agent rollups; Overview setup card + phase checklist. |
| 2026-04-22 | **Beginner path** doc: Serving + inference tables vs MLflow vs system tables; Lakebase deferred; no prefab “sample agents”. |

---

## Notes for future sessions

- Prefer **incremental PR-sized steps**: wire one data source at a time, keep UI graceful when tables are empty.
- **Never commit secrets** — use `.env` locally (gitignored) and Databricks secrets in deployment.
- Update the **“What exists”** and **“Changelog”** sections whenever a milestone completes.
