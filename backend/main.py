from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .gateway import router
from .store import NetworkXGraphStore

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = FastAPI(title="AI Codebase X-Ray", version="0.5.0", description="Gateway in front of knowledge, embedding, retrieval, graph, and git services")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
ANALYSIS_DIR = Path(__file__).resolve().parent.parent / "data" / "analyses"
store = NetworkXGraphStore(ANALYSIS_DIR)

app.include_router(router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "AI Codebase X-Ray"}


# Keep the catch-all static route after API routes so `/api/*` remains reachable.
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
