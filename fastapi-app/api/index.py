"""FastAPI application served as a Vercel Python function.

Vercel's Python runtime looks for a module-level ASGI object named `app`
in one of its entrypoint locations; `api/index.py` is the conventional
one, and `vercel.json` rewrites every path here so FastAPI owns routing.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_NAME = os.getenv("APP_NAME", "fastapi-on-vercel")
# Comma-separated list, e.g. "https://example.com,https://www.example.com".
# Defaults to "*" so the fresh deployment is callable from anywhere; tighten
# this in the Vercel dashboard before putting anything real behind it.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(
    title=APP_NAME,
    version="0.1.0",
    description="FastAPI boilerplate deployed as a Vercel serverless function.",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Health(BaseModel):
    status: str = "ok"
    app: str
    timestamp: datetime


class EchoIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class EchoOut(BaseModel):
    original: str
    reversed: str
    length: int


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "app": APP_NAME,
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health", response_model=Health, tags=["meta"])
def health() -> Health:
    """Liveness probe — cheap, dependency-free, safe to poll."""
    return Health(app=APP_NAME, timestamp=datetime.now(timezone.utc))


@app.post("/api/echo", response_model=EchoOut, tags=["demo"])
def echo(payload: EchoIn) -> EchoOut:
    """Sample endpoint showing request validation and a typed response."""
    return EchoOut(
        original=payload.message,
        reversed=payload.message[::-1],
        length=len(payload.message),
    )


@app.get("/api/items/{item_id}", tags=["demo"])
def get_item(item_id: int, q: str | None = None) -> dict:
    """Sample path + query parameters, with a real error path."""
    if item_id < 1:
        raise HTTPException(status_code=400, detail="item_id must be >= 1")
    return {"item_id": item_id, "q": q}
