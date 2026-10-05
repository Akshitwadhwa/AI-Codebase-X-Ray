from __future__ import annotations

import json
from pathlib import Path

from ..models import AnalysisResult, KnowledgeView
from .embedding import EmbeddingService

MAX_CHUNKS = 400
WINDOW = 8


class KnowledgeChunk:
    def __init__(self, path: str, start_line: int, end_line: int, kind: str, text: str, vector: list[float] | None = None) -> None:
        self.path = path
        self.start_line = start_line
        self.end_line = end_line
        self.kind = kind
        self.text = text
        self.vector = vector or []


class KnowledgeIndex:
    def __init__(self, analysis_id: str, chunks: list[KnowledgeChunk], embedding: str, dimension: int) -> None:
        self.analysis_id = analysis_id
        self.chunks = chunks
        self.embedding = embedding
        self.dimension = dimension

    def view(self) -> KnowledgeView:
        paths = list(dict.fromkeys(chunk.path for chunk in self.chunks))
        return KnowledgeView(
            analysis_id=self.analysis_id,
            embedding=self.embedding,
            dimension=self.dimension,
            documents=len({chunk.path for chunk in self.chunks if chunk.kind == "document"}),
            chunks=len(self.chunks),
            paths=paths[:80],
        )


def _windows(text: str) -> list[tuple[int, int, str]]:
    rows = text.splitlines()
    if not rows:
        return []
    windows: list[tuple[int, int, str]] = []
    start = 0
    while start < len(rows):
        end = min(len(rows), start + WINDOW)
        body = "\n".join(rows[start:end]).strip()
        if body:
            windows.append((start + 1, end, body))
        if end == len(rows):
            break
        start += WINDOW
    return windows


def build_chunks(result: AnalysisResult) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for document in result.documents:
        for start, end, body in _windows(document.text):
            chunks.append(KnowledgeChunk(document.path, start, end, "document", body))
    for item in result.imports:
        chunks.append(KnowledgeChunk(item.file_path, item.line, item.line, "import", f"import {item.imported_name}"))
    for symbol in result.symbols:
        label = f"{symbol.kind} {symbol.parent + '.' if symbol.parent else ''}{symbol.name}"
        body = f"{label}\n{symbol.snippet}".strip() if symbol.snippet else label
        chunks.append(KnowledgeChunk(symbol.file_path, symbol.start_line, symbol.end_line, "symbol", body))
    return chunks[:MAX_CHUNKS]


class KnowledgeService:
    def __init__(self, directory: Path | None = None, embedding: EmbeddingService | None = None) -> None:
        self.directory = directory
        self.embedding = embedding or EmbeddingService()

    def _path(self, analysis_id: str) -> Path | None:
        if self.directory is None or not analysis_id:
            return None
        return self.directory / f"{analysis_id}.knowledge.json"

    def index(self, result: AnalysisResult) -> KnowledgeIndex:
        cached = self.load(result.analysis_id or "")
        if cached is not None:
            return cached
        chunks = build_chunks(result)
        vectors = self.embedding.embed_texts([chunk.text for chunk in chunks]) if chunks else []
        for chunk, vector in zip(chunks, vectors):
            chunk.vector = vector
        knowledge = KnowledgeIndex(result.analysis_id or "", chunks, self.embedding.name, self.embedding.dimension)
        self.save(knowledge)
        return knowledge

    def save(self, knowledge: KnowledgeIndex) -> None:
        path = self._path(knowledge.analysis_id)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "analysis_id": knowledge.analysis_id,
            "embedding": knowledge.embedding,
            "dimension": knowledge.dimension,
            "chunks": [
                {
                    "path": chunk.path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "kind": chunk.kind,
                    "text": chunk.text,
                    "vector": chunk.vector,
                }
                for chunk in knowledge.chunks
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def load(self, analysis_id: str) -> KnowledgeIndex | None:
        path = self._path(analysis_id)
        if path is None or not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("embedding") != self.embedding.name:
            return None
        chunks = [
            KnowledgeChunk(item["path"], item["start_line"], item["end_line"], item["kind"], item["text"], item.get("vector") or [])
            for item in payload.get("chunks") or []
        ]
        return KnowledgeIndex(payload.get("analysis_id") or analysis_id, chunks, payload["embedding"], payload["dimension"])
