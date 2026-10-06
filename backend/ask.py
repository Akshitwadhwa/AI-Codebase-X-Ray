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
    "backend": ["fastapi", "flask", "django", "express", "backend", "uvicorn", "python", "javascript"],
    "server": ["fastapi", "express", "flask", "uvicorn"],
    "made": ["fastapi", "flask", "django", "express", "uvicorn", "python", "javascript", "typescript", "framework"],
    "built": ["fastapi", "flask", "django", "express", "uvicorn", "python", "javascript", "framework"],
    "stack": ["fastapi", "flask", "django", "express", "uvicorn", "python", "javascript", "framework"],
    "framework": ["fastapi", "flask", "django", "express", "uvicorn"],
}
FRAMEWORKS = ("fastapi", "flask", "django", "express", "uvicorn", "starlette", "nestjs")


def question_terms(question: str) -> set[str]:
    terms = {token for token in re.findall(r"[a-z0-9_.]+", question.lower()) if len(token) > 2 and token not in STOP}
    expanded = set(terms)
    skip = set() if _asks_about_backend(question) else {"made", "built"}
    for token in list(terms):
        if token in skip:
            continue
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


def _is_dependency(path: str) -> bool:
    name = path.lower()
    return name.endswith(("requirements.txt", "package.json", "pyproject.toml")) or "requirement" in name


def _framework_evidence(result: AnalysisResult) -> tuple[list, list[tuple[str, int, str]], list[str]]:
    imports = []
    dependencies: list[tuple[str, int, str]] = []
    names: list[str] = []

    def add(name: str) -> None:
        if name not in names:
            names.append(name)

    for item in result.imports:
        for name in FRAMEWORKS:
            if name in item.imported_name.lower():
                imports.append(item)
                add(name)
                break
    for document in result.documents:
        if not _is_dependency(document.path):
            continue
        for number, line in enumerate(document.text.splitlines()[:40], 1):
            stripped = line.strip()
            if not stripped:
                continue
            matched = [name for name in FRAMEWORKS if name in stripped.lower()]
            if matched:
                dependencies.append((document.path, number, stripped))
            for name in matched:
                add(name)
    return imports, dependencies, names


def _stack_lines(result: AnalysisResult) -> list[str]:
    language = result.summary.primary_language or "unknown"
    imports, _matched, names = _framework_evidence(result)
    framework = f" Frameworks: {', '.join(names)}." if names else ""
    lines = [
        f"[scan-summary:1] Primary language: {language}. Files: {result.summary.files}. Classes: {result.summary.classes}. Functions: {result.summary.functions}. Branch: {result.default_branch}.{framework}"
    ]
    for document in result.documents:
        if not _is_dependency(document.path):
            continue
        for number, line in enumerate(document.text.splitlines()[:20], 1):
            if line.strip():
                lines.append(f"[{document.path}:{number}] {line.strip()}")
    for item in imports:
        lines.append(f"[{item.file_path}:{item.line}] import {item.imported_name}")
    return lines


def _question_tokens(question: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_.]+", question.lower()) if len(token) > 2 and token not in STOP}


def _asks_about_backend(question: str) -> bool:
    return bool(_question_tokens(question) & {"backend", "server", "stack", "framework", "language"})


def _asks_about_gitignore(question: str) -> bool:
    return any("gitignore" in token for token in _question_tokens(question))


def stack_facts(result: AnalysisResult) -> ModelAnswer | None:
    language = result.summary.primary_language
    imports, dependencies, names = _framework_evidence(result)
    if not language and not names and not dependencies:
        return None
    sentences: list[str] = []
    citations: list[Citation] = []
    seen: set[tuple[str, int | None]] = set()

    def cite(path: str, line: int) -> None:
        key = (path, line)
        if key in seen:
            return
        seen.add(key)
        citations.append(Citation(path=path, line=line))

    if language:
        sentences.append(f"The backend is {language}.")
    if names:
        sentences.append("Frameworks in the scan: " + ", ".join(names) + ".")
    for path, line, text in dependencies[:8]:
        cite(path, line)
        sentences.append(f"{path}:{line} lists {text}.")
    for item in imports:
        cite(item.file_path, item.line)
        sentences.append(f"{item.file_path}:{item.line} imports {item.imported_name}.")
    if language and not citations:
        extension = {"Python": ".py", "JavaScript": ".js", "TypeScript": ".ts"}.get(language)
        source = next((path for path in result.files if extension and path.endswith(extension)), None)
        if source:
            cite(source, 1)
    return ModelAnswer(answer=" ".join(sentences), citations=citations[:6], provider="offline", refused=False, mode="grounded")


def _gitignore_lines(result: AnalysisResult) -> list[str]:
    lines: list[str] = []
    for document in result.documents:
        if not document.path.lower().endswith(".gitignore"):
            continue
        for number, line in enumerate(document.text.splitlines()[:40], 1):
            if line.strip():
                lines.append(f"[{document.path}:{number}] {line.strip()}")
    return lines


def gitignore_facts(result: AnalysisResult) -> ModelAnswer | None:
    quoted: list[tuple[str, int, str]] = []
    for document in result.documents:
        if not document.path.lower().endswith(".gitignore"):
            continue
        for number, line in enumerate(document.text.splitlines()[:40], 1):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                quoted.append((document.path, number, stripped))
    if not quoted:
        return None
    citations = [Citation(path=path, line=number) for path, number, _text in quoted[:6]]
    patterns = ", ".join(text for _path, _number, text in quoted[:8])
    return ModelAnswer(
        answer=f"The .gitignore is made up of: {patterns}.",
        citations=citations,
        provider="offline",
        refused=False,
        mode="grounded",
    )


def _mentions_gitignore(answer: ModelAnswer, patterns: list[str]) -> bool:
    if any(item.path.lower().endswith(".gitignore") for item in answer.citations):
        return True
    blob = answer.answer.lower()
    return any(pattern.lower() in blob for pattern in patterns)


def _with_topic_facts(answer: ModelAnswer, result: AnalysisResult, question: str) -> ModelAnswer:
    if answer.mode == "baseline":
        return answer
    if _asks_about_gitignore(question):
        facts = gitignore_facts(result)
        patterns = [part.strip() for part in facts.answer.split(":", 1)[-1].split(",")] if facts is not None else []
        if facts is not None and (answer.refused or not _mentions_gitignore(answer, patterns)):
            return ModelAnswer(answer=facts.answer, citations=facts.citations, provider=answer.provider, refused=False, mode=answer.mode)
        return answer
    if not answer.refused or not _asks_about_backend(question):
        return answer
    facts = stack_facts(result)
    if facts is None:
        return answer
    return ModelAnswer(answer=facts.answer, citations=facts.citations, provider=answer.provider, refused=False, mode=answer.mode)


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
        "Context lines are prefixed with [path:line]. The scan-summary line and dependency imports are evidence for what the backend is built with.",
    ]
    seen: set[str] = set()
    topic_lines = _gitignore_lines(result) if _asks_about_gitignore(question) else []
    for line in [*topic_lines, *_stack_lines(result), *_vector_lines(result, question)]:
        if line not in seen:
            seen.add(line)
            lines.append(line)
    if len(lines) > 8:
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
        if path == "scan-summary":
            continue
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
    grounded = pack_context(result, question, baseline=False)
    answers = [_with_topic_facts(complete(provider, grounded, "grounded"), result, question)]
    if compare_with == "without-rag":
        answers.append(answer_baseline(result, question, provider))
    elif compare_with:
        if compare_with == provider:
            raise ProviderError("Choose a different provider to compare")
        answers.append(_with_topic_facts(complete(compare_with, grounded, "grounded"), result, question))
    return AskResponse(question=question, answers=answers)


def answer_baseline(result: AnalysisResult, question: str, provider: str) -> ModelAnswer:
    return complete(provider, pack_context(result, question, baseline=True), "baseline")


def symbol_prompt(report) -> str:
    symbol = report.symbol
    lines = [
        f"Repository: {report.symbol.file_path}",
        "Question: What does this function do, and which files call it?",
        "Use only the evidence below. Do not invent callers.",
        f"[{symbol.file_path}:{symbol.start_line}] {symbol.kind} {symbol.parent + '.' if symbol.parent else ''}{symbol.name}",
    ]
    if symbol.snippet:
        for offset, line in enumerate(symbol.snippet.splitlines()):
            if line.strip():
                lines.append(f"[{symbol.file_path}:{symbol.start_line + offset}] {line.strip()}")
    direct = [item for item in report.callers if item.depth == 1]
    if direct:
        lines.append("Callers:")
        for item in direct:
            lines.append(f"[{item.file_path}:{item.line or 1}] {item.name} calls {symbol.name}")
    else:
        lines.append("Callers: none resolved")
    return "\n".join(lines)


def explain_symbol(report, provider: str) -> ModelAnswer:
    symbol = report.symbol
    if provider == "offline":
        direct = [item for item in report.callers if item.depth == 1 and item.file_path]
        if direct:
            called = "; ".join(f"{item.name} in {item.file_path}" for item in direct)
            caller_sentence = f"It is called from {called}."
        else:
            caller_sentence = "No resolved caller was found in the graph."
        place = f"inside {symbol.parent}, " if symbol.parent else ""
        snippet = f" The saved code is: {symbol.snippet.strip()}" if symbol.snippet else ""
        citations = [Citation(path=symbol.file_path, line=symbol.start_line)] if symbol.file_path else []
        citations.extend(Citation(path=item.file_path, line=item.line) for item in direct if item.file_path)
        return ModelAnswer(
            answer=f"{symbol.name} is a {symbol.kind} {place}in {symbol.file_path}.{snippet} {caller_sentence}",
            citations=citations,
            provider="offline",
            refused=False,
            mode="grounded",
        )
    return complete(provider, symbol_prompt(report), "grounded")
