from __future__ import annotations

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    repository_url: str = Field(..., min_length=1, description="Public GitHub repository URL")


class Symbol(BaseModel):
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    parent: str | None = None
    symbol_id: str | None = None
    snippet: str | None = None


class ImportRecord(BaseModel):
    file_path: str
    imported_name: str
    line: int
    resolved_file: str | None = None


class Relationship(BaseModel):
    source: str
    target: str
    kind: str
    line: int | None = None


class AnalysisSummary(BaseModel):
    files: int
    folders: int
    source_files: int
    languages: dict[str, int]
    primary_language: str | None = None
    classes: int
    functions: int
    imports: int
    tests: int


class DocumentExcerpt(BaseModel):
    path: str
    text: str


class AnalysisResult(BaseModel):
    analysis_id: str | None = None
    repository: str
    default_branch: str
    summary: AnalysisSummary
    files: list[str]
    symbols: list[Symbol]
    imports: list[ImportRecord]
    relationships: list[Relationship]
    documents: list[DocumentExcerpt] = []
    warnings: list[str] = []


class Citation(BaseModel):
    path: str
    line: int | None = None


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    provider: str = "offline"
    compare_with: str | None = None


class ModelAnswer(BaseModel):
    answer: str
    citations: list[Citation] = []
    provider: str
    refused: bool = False
    mode: str


class AskResponse(BaseModel):
    question: str
    answers: list[ModelAnswer]


class GraphNode(BaseModel):
    id: str
    kind: str
    name: str
    file_path: str | None = None
    parent: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    snippet: str | None = None
    is_test: bool = False
    symbol_count: int = 0


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: str
    confidence: str | None = None
    line: int | None = None
    raw: str | None = None


class UnresolvedRef(BaseModel):
    source: str
    raw: str
    kind: str
    line: int | None = None
    candidates: list[str] = []


class GraphDocument(BaseModel):
    analysis_id: str
    repository: str
    default_branch: str
    warnings: list[str] = []
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    unresolved: list[UnresolvedRef] = []


class SymbolView(BaseModel):
    symbol_id: str
    name: str
    kind: str
    file_path: str | None = None
    parent: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    snippet: str | None = None


class EvidenceLink(BaseModel):
    symbol_id: str | None = None
    name: str
    kind: str | None = None
    file_path: str | None = None
    raw: str | None = None
    confidence: str | None = None
    line: int | None = None
    depth: int = 1
    reason: str
    snippet: str | None = None


class ReviewFile(BaseModel):
    path: str
    reasons: list[str]


class RiskFlag(BaseModel):
    code: str
    message: str


class ImpactReport(BaseModel):
    symbol_id: str
    symbol: SymbolView
    callers: list[EvidenceLink]
    callees: list[EvidenceLink]
    bases: list[EvidenceLink]
    imports: list[EvidenceLink]
    tests: list[EvidenceLink]
    review_files: list[ReviewFile]
    risk: list[RiskFlag]
    unresolved: list[EvidenceLink]
    warnings: list[str] = []
    depth: int = 1


class ViewNode(BaseModel):
    id: str
    label: str
    kind: str
    file_path: str | None = None
    role: str | None = None
    is_test: bool = False
    symbol_count: int = 0


class ViewEdge(BaseModel):
    source: str
    target: str
    kind: str
    confidence: str | None = None
    label: str


class GraphView(BaseModel):
    view: str
    truncated: bool = False
    nodes: list[ViewNode]
    edges: list[ViewEdge]


class FileView(BaseModel):
    path: str
    is_test: bool
    symbols: list[SymbolView]
    imports: list[EvidenceLink]
    imported_by: list[EvidenceLink]
    tests: list[EvidenceLink]


class AnalysisIndexEntry(BaseModel):
    analysis_id: str
    repository: str
    default_branch: str
    primary_language: str | None = None
    files: int
    functions: int
