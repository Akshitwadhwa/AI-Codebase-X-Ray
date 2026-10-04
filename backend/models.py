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


class ImportRecord(BaseModel):
    file_path: str
    imported_name: str
    line: int
    resolved_file: str | None = None


class Relationship(BaseModel):
    source: str
    target: str
    kind: str


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


class AnalysisResult(BaseModel):
    analysis_id: str | None = None
    repository: str
    default_branch: str
    summary: AnalysisSummary
    files: list[str]
    symbols: list[Symbol]
    imports: list[ImportRecord]
    relationships: list[Relationship]
    warnings: list[str] = []
