"""CyberAI Framework Management System — API entrypoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import catalog, controls
from .settings import CORS_ORIGINS, DATABASE_PATH

app = FastAPI(
    title="CyberAI Framework Management System",
    version="1.0.0",
    description="Internal API for managing the CyberAI framework catalog.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalog.router, prefix="/api", tags=["catalog"])
app.include_router(controls.router, prefix="/api", tags=["controls"])


@app.get("/api/health")
def health():
    return {"status": "ok", "database": str(DATABASE_PATH), "exists": DATABASE_PATH.exists()}


@app.get("/")
def root():
    return {"service": "cyberai-fms", "docs": "/docs"}
