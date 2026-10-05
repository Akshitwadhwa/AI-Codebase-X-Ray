from __future__ import annotations

import re

from .models import AnalysisResult, AskResponse, Citation, ModelAnswer
from .providers import ProviderError, complete

STOP = {"the", "and", "for", "with", "this", "that", "what", "how", "does", "use", "used", "from", "are", "all", "about", "repo", "repository"}
TOPIC_TERMS = {
    "database": ["sqlite", "sqlalchemy", "postgres", "postgresql", "mysql", "mongo", "mongodb", "redis", "prisma", "database", "db"],
    "db": ["sqlite", "sqlalchemy", "postgres", "mysql", "mongo", "redis", "prisma", "database"],
    "sql": ["sqlite", "sqlalchemy", "postgres", "mysql", "prisma"],
    "orm": ["prisma", "sqlalchemy", "typeorm", "orm"],
    "gitignore": ["gitignore", ".env", "node_modules", "pycache"],
    "ignore": ["gitignore", ".env", "node_modules"],
    "hidden": ["gitignore", ".env", "node_modules"],
    "backend": ["fastapi", "flask", "django", "express", "backend", "uvicorn"],
    "server": ["fastapi", "express", "flask", "uvicorn"],
}


def question_terms(question: str) -> set[str]:
    terms = {token for token in re.findall(r"[a-z0-9_.]+", question.lower()) if len(token) > 2 and token not in STOP}
    expanded = set(terms)
    for token in list(terms):
        expanded.update(TOPIC_TERMS.get(token, []))
    return expanded


def _line_hits(text: str, terms: set[str], limit: int = 12) -> list[tuple[int, str]]:
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        lowered = line.lower()
        if terms and any(term in lowered for term in terms):
            hits.append((number, line.strip()))
        if len(hits) >= limit:
            break
    return hits


def _vector_lines(result: AnalysisResult, question: str) -> list[str]:
    from .services.orchestrator import Orchestrator

    lines: list[str] = []
    for chunk in Orchestrator().retrieve(result, question).chunks:
        for offset, line in enumerate(chunk.text.splitlines()):
            if line.strip():
                lines.append(f"[{chunk.path}:{chunk.start_line + offset}] {line.strip()}")
    return lines


def pack_context(result: AnalysisResult, question: str, baseline: bool = False) -> str:
    if baseline:
        return f"Repository: {result.repository}\nNo source files, documents, or symbols were provided.\nQuestion: {question}"
    terms = question_terms(question)
    lines = [
        f"Repository: {result.repository}",
        f"Branch: {result.default_branch}",
        f"Primary language: {result.summary.primary_language or 'unknown'}",
        f"Files: {result.summary.files}. Source files: {result.summary.source_files}.",
        "Question: " + question,
        "",
        "Context lines are prefixed with [path:line].",
    ]
    retrieved = _vector_lines(result, question)
    if retrieved:
        lines.extend(retrieved[:40])
        return "\n".join(lines)[:12000]
    for document in result.documents:
        hits = _line_hits(document.text, terms) or [(number, line.strip()) for number, line in enumerate(document.text.splitlines()[:8], 1) if line.strip()]
        if not hits:
            continue
        lines.append(f"## document: {document.path}")
        lines.extend(f"[{document.path}:{number}] {line}" for number, line in hits[:12])
    import_hits = []
    for item in result.imports:
        blob = f"{item.imported_name} {item.file_path}".lower()
        if not terms or any(term in blob for term in terms):
            import_hits.append(f"[{item.file_path}:{item.line}] import {item.imported_name}")
    if import_hits:
        lines.append("## imports")
        lines.extend(import_hits[:40])
    symbol_hits = []
    for symbol in result.symbols:
        blob = f"{symbol.name} {symbol.file_path} {symbol.parent or ''}".lower()
        if terms and any(term in blob for term in terms):
            symbol_hits.append(f"[{symbol.file_path}:{symbol.start_line}] {symbol.kind} {symbol.parent + '.' if symbol.parent else ''}{symbol.name}")
    if symbol_hits:
        lines.append("## symbols")
        lines.extend(symbol_hits[:40])
    if result.warnings:
        lines.append("## warnings")
        lines.extend(result.warnings[:5])
    return "\n".join(lines)[:12000]


def offline_answer(prompt: str, mode: str) -> ModelAnswer:
    if mode == "baseline" or "No source files" in prompt:
        return ModelAnswer(
            answer="Only the repository name was provided, so this answer cannot cite files from the scan.",
            provider="offline",
            refused=True,
            mode=mode,
        )
    question = ""
    for line in prompt.splitlines():
        if line.startswith("Question: "):
            question = line.removeprefix("Question: ")
            break
    terms = question_terms(question)
    citations: list[Citation] = []
    chosen: list[str] = []
    seen: set[tuple[str, int | None]] = set()
    for line in prompt.splitlines():
        match = re.match(r"\[([^:\]]+):(\d+)\]\s*(.*)", line)
        if not match:
            continue
        path, line_no, text = match.group(1), int(match.group(2)), match.group(3).strip()
        if terms and not any(term in text.lower() or term in path.lower() for term in terms):
            continue
        key = (path, line_no)
        if key in seen:
            continue
        seen.add(key)
        citations.append(Citation(path=path, line=line_no))
        chosen.append(f"{path}:{line_no} {text}")
        if len(citations) >= 6:
            break
    if not chosen:
        return ModelAnswer(answer="Not in this scan. The saved excerpts do not contain an answer.", provider="offline", refused=True, mode=mode)
    return ModelAnswer(answer=" ".join(chosen), citations=citations, provider="offline", refused=False, mode=mode)


def answer_question(result: AnalysisResult, question: str, provider: str, compare_with: str | None = None) -> AskResponse:
    answers = [complete(provider, pack_context(result, question, baseline=False), "grounded")]
    if compare_with == "without-rag":
        answers.append(answer_baseline(result, question, provider))
    elif compare_with:
        if compare_with == provider:
            raise ProviderError("Choose a different provider to compare")
        answers.append(complete(compare_with, pack_context(result, question, baseline=False), "grounded"))
    return AskResponse(question=question, answers=answers)


def answer_baseline(result: AnalysisResult, question: str, provider: str) -> ModelAnswer:
    return complete(provider, pack_context(result, question, baseline=True), "baseline")
