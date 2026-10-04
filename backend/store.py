from __future__ import annotations

import re
from pathlib import Path

from .analyzer import assign_symbol_ids
from .graph import build_graph
from .models import AnalysisIndexEntry, AnalysisResult, GraphDocument

ANALYSIS_ID = re.compile(r"^[0-9a-f]{12}$")


class InvalidAnalysisId(ValueError):
    """The analysis id is not the 12-hex id written by Phase 1."""


class InvalidFilePath(ValueError):
    """The file path is absolute or escapes the analysis."""


def safe_relative_path(path: str) -> str:
    if not path or path.startswith(("/", "\\")) or "\\" in path or path.startswith("~"):
        raise InvalidFilePath(path)
    parts = Path(path).parts
    if not parts or ".." in parts:
        raise InvalidFilePath(path)
    return Path(path).as_posix()


class NetworkXGraphStore:
    """Analysis JSON is the source of truth. The graph file is a cache."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def validate(self, analysis_id: str) -> str:
        if not ANALYSIS_ID.fullmatch(analysis_id or ""):
            raise InvalidAnalysisId(analysis_id)
        return analysis_id

    def analysis_path(self, analysis_id: str) -> Path:
        return self.directory / f"{self.validate(analysis_id)}.json"

    def graph_path(self, analysis_id: str) -> Path:
        return self.directory / f"{self.validate(analysis_id)}.graph.json"

    def save_analysis(self, result: AnalysisResult) -> None:
        if not result.analysis_id:
            raise InvalidAnalysisId("")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.analysis_path(result.analysis_id).write_text(result.model_dump_json(indent=2), encoding="utf-8")

    def load_analysis(self, analysis_id: str) -> AnalysisResult | None:
        path = self.analysis_path(analysis_id)
        if not path.is_file():
            return None
        result = AnalysisResult.model_validate_json(path.read_text(encoding="utf-8"))
        result.analysis_id = analysis_id
        assign_symbol_ids(result.symbols)
        return result

    def save_graph(self, document: GraphDocument) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.graph_path(document.analysis_id).write_text(document.model_dump_json(indent=2), encoding="utf-8")

    def load_graph(self, analysis_id: str) -> GraphDocument | None:
        if not self.analysis_path(analysis_id).is_file():
            return None
        cached = self.graph_path(analysis_id)
        if cached.is_file():
            try:
                return GraphDocument.model_validate_json(cached.read_text(encoding="utf-8"))
            except ValueError:
                cached.unlink(missing_ok=True)
        result = self.load_analysis(analysis_id)
        if result is None:
            return None
        document = build_graph(result)
        document.analysis_id = analysis_id
        self.save_graph(document)
        return document

    def list_analyses(self, limit: int = 20) -> list[AnalysisIndexEntry]:
        if not self.directory.is_dir():
            return []
        files = [path for path in self.directory.glob("*.json") if not path.name.endswith(".graph.json")]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        entries: list[AnalysisIndexEntry] = []
        for path in files[:limit]:
            analysis_id = path.stem
            if not ANALYSIS_ID.fullmatch(analysis_id):
                continue
            try:
                result = AnalysisResult.model_validate_json(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            entries.append(AnalysisIndexEntry(
                analysis_id=analysis_id,
                repository=result.repository,
                default_branch=result.default_branch,
                primary_language=result.summary.primary_language,
                files=result.summary.files,
                functions=result.summary.functions,
            ))
        return entries
