from __future__ import annotations

from pathlib import Path

from ..ask import answer_question
from ..models import AnalysisResult, AskResponse, KnowledgeView, RetrievalResponse
from .embedding import EmbeddingService
from .knowledge import KnowledgeService
from .retrieval import RetrievalService


class Orchestrator:
    """One request moves from the knowledge index through embedding and retrieval."""

    def __init__(self, directory: Path | None = None) -> None:
        self.embedding = EmbeddingService()
        self.knowledge = KnowledgeService(directory, self.embedding)
        self.retrieval = RetrievalService(self.knowledge, self.embedding)

    def index(self, result: AnalysisResult) -> KnowledgeView:
        return self.knowledge.index(result).view()

    def retrieve(self, result: AnalysisResult, question: str, k: int = 8) -> RetrievalResponse:
        found = self.retrieval.retrieve(self.knowledge.index(result), question, k)
        return RetrievalResponse(question=question, embedding=self.embedding.name, chunks=found)

    def ask(self, result: AnalysisResult, question: str, provider: str, compare_with: str | None = None) -> AskResponse:
        self.knowledge.index(result)
        return answer_question(result, question, provider, compare_with)
