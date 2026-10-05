from __future__ import annotations

from ..models import RetrievedChunk
from .embedding import EmbeddingService
from .knowledge import KnowledgeIndex, KnowledgeService

MIN_SCORE = 0.08


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


class RetrievalService:
    def __init__(self, knowledge: KnowledgeService | None = None, embedding: EmbeddingService | None = None) -> None:
        self.embedding = embedding or EmbeddingService()
        self.knowledge = knowledge or KnowledgeService(embedding=self.embedding)

    def retrieve(self, index: KnowledgeIndex, question: str, k: int = 8) -> list[RetrievedChunk]:
        query = self.embedding.embed_query(question)
        ranked: list[RetrievedChunk] = []
        for chunk in index.chunks:
            score = cosine(query, chunk.vector)
            if score < MIN_SCORE:
                continue
            ranked.append(RetrievedChunk(
                path=chunk.path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                kind=chunk.kind,
                text=chunk.text,
                score=round(score, 4),
            ))
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:k]
