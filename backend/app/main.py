"""AgentOps Dashboard — FastAPI entrypoint (local + Databricks Apps)."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# Load `backend/.env` regardless of process cwd (uvicorn from repo root, IDE, etc.).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.services.databricks_status import sql_probe

app = FastAPI(
    title="AgentOps Dashboard API",
    description="Accelerator backend: health, cost, quality, governance aggregates.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5175",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, object]:
    db = sql_probe()
    return {
        "status": "ok",
        "service": "agentops-api",
        "databricks": db,
    }


app.include_router(v1_router, prefix="/api/v1")
