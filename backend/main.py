from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .analyzer import analyze_source
from .github import RepositoryError, download_repository, parse_repository_url
from .graph import architecture_view, build_graph, file_view, impact_report, neighborhood_view, search_symbols
from .models import AnalysisIndexEntry, AnalysisResult, AnalyzeRequest, FileView, GraphView, ImpactReport, SymbolView
from .store import InvalidAnalysisId, InvalidFilePath, NetworkXGraphStore, safe_relative_path

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("codebase-xray")
app = FastAPI(title="AI Codebase X-Ray", version="0.2.0", description="Repository graph and impact review")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
ANALYSIS_DIR = Path(__file__).resolve().parent.parent / "data" / "analyses"
store = NetworkXGraphStore(ANALYSIS_DIR)


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
            store.save_analysis(result)
            try:
                document = build_graph(result)
                document.analysis_id = result.analysis_id
                store.save_graph(document)
            except Exception:
                logger.exception("Graph cache was not written for %s", result.analysis_id)
            return result
    except RepositoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unexpected analysis failure")
        raise HTTPException(status_code=500, detail="Analysis failed unexpectedly. Check the server logs")


def _analysis_or_404(analysis_id: str) -> str:
    try:
        return store.validate(analysis_id)
    except InvalidAnalysisId as exc:
        raise HTTPException(status_code=400, detail="Analysis id must be 12 hex characters") from exc


def _graph_or_404(analysis_id: str):
    _analysis_or_404(analysis_id)
    try:
        document = store.load_graph(analysis_id)
    except InvalidAnalysisId as exc:
        raise HTTPException(status_code=400, detail="Analysis id must be 12 hex characters") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Analysis was not found")
    return document


@app.get("/api/analyses", response_model=list[AnalysisIndexEntry])
def list_analyses() -> list[AnalysisIndexEntry]:
    return store.list_analyses()


@app.get("/api/analyses/{analysis_id}", response_model=AnalysisResult)
def get_analysis(analysis_id: str) -> AnalysisResult:
    _analysis_or_404(analysis_id)
    result = store.load_analysis(analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis was not found")
    return result


@app.get("/api/analyses/{analysis_id}/symbols", response_model=list[SymbolView])
def find_symbols(analysis_id: str, q: str = Query("", max_length=200)) -> list[SymbolView]:
    return search_symbols(_graph_or_404(analysis_id), q)


@app.get("/api/analyses/{analysis_id}/graph", response_model=GraphView)
def get_graph(analysis_id: str, view: str = Query("architecture"), symbol_id: str | None = None) -> GraphView:
    document = _graph_or_404(analysis_id)
    if view == "neighborhood":
        if not symbol_id:
            raise HTTPException(status_code=400, detail="symbol_id is required for a neighborhood graph")
        neighborhood = neighborhood_view(document, symbol_id)
        if neighborhood is None:
            raise HTTPException(status_code=404, detail="Symbol was not found in this analysis")
        return neighborhood
    if view != "architecture":
        raise HTTPException(status_code=400, detail="view must be architecture or neighborhood")
    return architecture_view(document)


@app.get("/api/analyses/{analysis_id}/impact/{symbol_id:path}", response_model=ImpactReport)
def get_impact(analysis_id: str, symbol_id: str, depth: int = Query(1)) -> ImpactReport:
    document = _graph_or_404(analysis_id)
    report = impact_report(document, symbol_id, depth)
    if report is None:
        raise HTTPException(status_code=404, detail="Symbol was not found in this analysis")
    return report


@app.get("/api/analyses/{analysis_id}/file", response_model=FileView)
def get_file(analysis_id: str, path: str = Query(..., min_length=1)) -> FileView:
    try:
        relative = safe_relative_path(path)
    except InvalidFilePath as exc:
        raise HTTPException(status_code=400, detail="File path must stay inside the analysis") from exc
    viewed = file_view(_graph_or_404(analysis_id), relative)
    if viewed is None:
        raise HTTPException(status_code=404, detail="File was not found in this analysis")
    return viewed


# Keep the catch-all static route after API routes so `/api/*` remains reachable.
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
