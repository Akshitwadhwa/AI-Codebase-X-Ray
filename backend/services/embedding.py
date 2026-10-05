from __future__ import annotations

import hashlib
import math
import re

from ..ask import STOP, TOPIC_TERMS

EMBEDDING_NAME = "local-hash-v1"
DIMENSION = 256


def _tokens(text: str) -> list[str]:
    found = re.findall(r"[a-z0-9_.]+", text.lower())
    expanded: list[str] = []
    for token in found:
        if len(token) <= 2 or token in STOP:
            continue
        expanded.append(token)
        expanded.extend(TOPIC_TERMS.get(token, []))
        for key, values in TOPIC_TERMS.items():
            if key in token:
                expanded.append(key)
                expanded.extend(values)
    return expanded


def _bucket(token: str) -> tuple[int, float]:
    digest = hashlib.sha1(token.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % DIMENSION
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    return index, sign


class EmbeddingService:
    """Local vector embeddings. A cloud or Ollama embedder can replace embed_texts later."""

    name = EMBEDDING_NAME
    dimension = DIMENSION

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            raw = [0.0] * DIMENSION
            for token in _tokens(text):
                index, sign = _bucket(token)
                raw[index] += sign
            norm = math.sqrt(sum(value * value for value in raw)) or 1.0
            vectors.append([round(value / norm, 6) for value in raw])
        return vectors

    def embed_query(self, question: str) -> list[float]:
        return self.embed_texts([question])[0]
