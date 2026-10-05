from __future__ import annotations

import logging
import tempfile
import uuid

from fastapi import APIRouter, HTTPException, Query

from .analyzer import analyze_source
from .github import RepositoryError, download_repository, parse_repository_url
from .ask import explain_symbol
from .graph import architecture_view, build_graph, file_view, impact_report, neighborhood_view, search_symbols
from .models import (
    AnalysisIndexEntry,
    AnalysisResult,
    AnalyzeRequest,
    AskRequest,
    AskResponse,
    ExplainRequest,
    FileView,
    GraphView,
    ImpactReport,
    KnowledgeView,
    ModelAnswer,
    RetrievalRequest,
    RetrievalResponse,
    SymbolView,
)
from .providers import ProviderError, configured_providers
from .services.orchestrator import Orchestrator
from .store import InvalidAnalysisId, InvalidFilePath, safe_relative_path

logger = logging.getLogger("codebase-xray")
router = APIRouter()


def _store():
    from . import main

    return main.store


def _orchestrator() -> Orchestrator:
    return Orchestrator(_store().directory)


def _analysis_or_404(analysis_id: str) -> str:
    try:
        return _store().validate(analysis_id)
    except InvalidAnalysisId as exc:
        raise HTTPException(status_code=400, detail="Analysis id must be 12 hex characters") from exc


def _load_analysis(analysis_id: str) -> AnalysisResult:
    _analysis_or_404(analysis_id)
    result = _store().load_analysis(analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis was not found")
    return result


def _graph_or_404(analysis_id: str):
    _analysis_or_404(analysis_id)
    try:
        document = _store().load_graph(analysis_id)
    except InvalidAnalysisId as exc:
        raise HTTPException(status_code=400, detail="Analysis id must be 12 hex characters") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Analysis was not found")
    return document


@router.get("/api/analyses", response_model=list[AnalysisIndexEntry])
def list_analyses() -> list[AnalysisIndexEntry]:
    return _store().list_analyses()


@router.post("/api/analyze", response_model=AnalysisResult)
def analyze(request: AnalyzeRequest) -> AnalysisResult:
    try:
        owner, repo = parse_repository_url(request.repository_url)
        with tempfile.TemporaryDirectory(prefix="codebase-xray-") as temporary:
            from pathlib import Path

            root = Path(temporary)
            branch = download_repository(owner, repo, root)
            source_roots = [item for item in (root / "source").iterdir() if item.is_dir()]
            source = source_roots[0] if source_roots else root / "source"
            logger.info("Analysing %s/%s (%s)", owner, repo, branch)
            result = analyze_source(source, f"{owner}/{repo}", branch)
            result.analysis_id = uuid.uuid4().hex[:12]
            _store().save_analysis(result)
            try:
                document = build_graph(result)
                document.analysis_id = result.analysis_id
                _store().save_graph(document)
            except Exception:
                logger.exception("Graph cache was not written for %s", result.analysis_id)
            try:
                _orchestrator().index(result)
            except Exception:
                logger.exception("Knowledge index was not written for %s", result.analysis_id)
            return result
    except RepositoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unexpected analysis failure")
        raise HTTPException(status_code=500, detail="Analysis failed unexpectedly. Check the server logs")


@router.get("/api/analyses/{analysis_id}", response_model=AnalysisResult)
def get_analysis(analysis_id: str) -> AnalysisResult:
    return _load_analysis(analysis_id)


@router.get("/api/analyses/{analysis_id}/knowledge", response_model=KnowledgeView)
def get_knowledge(analysis_id: str) -> KnowledgeView:
    return _orchestrator().index(_load_analysis(analysis_id))


@router.post("/api/analyses/{analysis_id}/retrieve", response_model=RetrievalResponse)
def retrieve_analysis(analysis_id: str, request: RetrievalRequest) -> RetrievalResponse:
    return _orchestrator().retrieve(_load_analysis(analysis_id), request.question.strip(), request.k)


@router.get("/api/analyses/{analysis_id}/symbols", response_model=list[SymbolView])
def find_symbols(analysis_id: str, q: str = Query("", max_length=200)) -> list[SymbolView]:
    return search_symbols(_graph_or_404(analysis_id), q)


@router.get("/api/analyses/{analysis_id}/graph", response_model=GraphView)
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


@router.post("/api/analyses/{analysis_id}/explain", response_model=ModelAnswer)
def explain_analysis_symbol(analysis_id: str, request: ExplainRequest) -> ModelAnswer:
    document = _graph_or_404(analysis_id)
    report = impact_report(document, request.symbol_id, depth=1)
    if report is None:
        raise HTTPException(status_code=404, detail="Symbol was not found in this analysis")
    try:
        return explain_symbol(report, request.provider)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/analyses/{analysis_id}/impact/{symbol_id:path}", response_model=ImpactReport)
def get_impact(analysis_id: str, symbol_id: str, depth: int = Query(1)) -> ImpactReport:
    document = _graph_or_404(analysis_id)
    report = impact_report(document, symbol_id, depth)
    if report is None:
        raise HTTPException(status_code=404, detail="Symbol was not found in this analysis")
    return report


@router.get("/api/providers")
def list_providers() -> list[dict[str, str | bool]]:
    return configured_providers()


@router.post("/api/analyses/{analysis_id}/ask", response_model=AskResponse)
def ask_analysis(analysis_id: str, request: AskRequest) -> AskResponse:
    try:
        return _orchestrator().ask(_load_analysis(analysis_id), request.question.strip(), request.provider, request.compare_with)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/analyses/{analysis_id}/file", response_model=FileView)
def get_file(analysis_id: str, path: str = Query(..., min_length=1)) -> FileView:
    try:
        relative = safe_relative_path(path)
    except InvalidFilePath as exc:
        raise HTTPException(status_code=400, detail="File path must stay inside the analysis") from exc
    viewed = file_view(_graph_or_404(analysis_id), relative)
    if viewed is None:
        raise HTTPException(status_code=404, detail="File was not found in this analysis")
    return viewed
