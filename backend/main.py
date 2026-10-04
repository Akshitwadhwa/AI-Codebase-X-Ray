from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .analyzer import analyze_source
from .github import RepositoryError, download_repository, parse_repository_url
from .models import AnalyzeRequest, AnalysisResult

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("codebase-xray")
app = FastAPI(title="AI Codebase X-Ray", version="0.1.0", description="Phase 1 repository intelligence demo")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
ANALYSIS_DIR = Path(__file__).resolve().parent.parent / "data" / "analyses"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "AI Codebase X-Ray"}


@app.post("/api/analyze", response_model=AnalysisResult)
def analyze(request: AnalyzeRequest) -> AnalysisResult:
    try:
        owner, repo = parse_repository_url(request.repository_url)
        with tempfile.TemporaryDirectory(prefix="codebase-xray-") as temporary:
            root = Path(temporary)
            branch = download_repository(owner, repo, root)
            source_roots = [item for item in (root / "source").iterdir() if item.is_dir()]
            source = source_roots[0] if source_roots else root / "source"
            logger.info("Analysing %s/%s (%s)", owner, repo, branch)
            result = analyze_source(source, f"{owner}/{repo}", branch)
            result.analysis_id = uuid.uuid4().hex[:12]
            ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
            (ANALYSIS_DIR / f"{result.analysis_id}.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
            return result
    except RepositoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unexpected analysis failure")
        raise HTTPException(status_code=500, detail="Analysis failed unexpectedly. Check the server logs")


# Keep the catch-all static route after API routes so `/api/*` remains reachable.
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
