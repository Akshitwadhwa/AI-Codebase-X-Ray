from __future__ import annotations

import ast
import os
import re
from collections import Counter
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_javascript as ts_javascript
import tree_sitter_python as ts_python
import tree_sitter_typescript as ts_typescript

from .models import AnalysisResult, AnalysisSummary, DocumentExcerpt, ImportRecord, Relationship, Symbol


IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", "coverage"}
LANGUAGES = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript"}
TEXT_EXTENSIONS = set(LANGUAGES) | {".md", ".json", ".yml", ".yaml", ".css", ".html"}
DOCUMENT_NAMES = {
    ".gitignore",
    "readme",
    "readme.md",
    "readme.rst",
    "readme.txt",
    "package.json",
    "pyproject.toml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "dockerfile",
}
EXCERPT_LINES = 200


def _tree_parser(language: str) -> Parser:
    if language == "Python":
        grammar = ts_python.language()
    elif language == "JavaScript":
        grammar = ts_javascript.language()
    else:
        # The TypeScript package exposes separate grammars for TS and TSX.
        grammar = ts_typescript.language_tsx() if language == "TypeScript" else ts_typescript.language_typescript()
    return Parser(Language(grammar))


def _node_name(node, source: bytes) -> str | None:
    name = node.child_by_field_name("name")
    return name.text.decode("utf-8", errors="replace") if name else None


def _snippet(source: bytes, start_line: int, end_line: int, limit: int = 30) -> str:
    lines = source.decode("utf-8", errors="replace").splitlines()
    start = max(0, start_line - 1)
    end = min(len(lines), max(start_line, end_line), start + limit)
    return "\n".join(lines[start:end])


def assign_symbol_ids(symbols: list[Symbol]) -> None:
    """Deterministic ids so a later graph can tell two same-named symbols apart."""
    bases: list[str] = []
    counts: dict[str, int] = {}
    for symbol in symbols:
        if symbol.kind == "method" and symbol.parent:
            base = f"sym:{symbol.file_path}::{symbol.parent}.{symbol.name}"
        else:
            base = f"sym:{symbol.file_path}::{symbol.name}"
        bases.append(base)
        counts[base] = counts.get(base, 0) + 1
    used: dict[str, int] = {}
    for symbol, base in zip(symbols, bases):
        candidate = f"{base}:{symbol.start_line}" if counts[base] > 1 else base
        if candidate in used:
            used[candidate] += 1
            candidate = f"{candidate}:{used[candidate]}"
        else:
            used[candidate] = 0
        symbol.symbol_id = candidate


def _tree_sitter_file(path: Path, relative: str, language: str) -> tuple[list[Symbol], list[ImportRecord]]:
    """Extract symbols/imports from the syntax tree, with no LLM involved."""
    source = path.read_bytes()
    tree = _tree_parser(language).parse(source)
    symbols: list[Symbol] = []
    imports: list[ImportRecord] = []
    class_nodes: list[tuple[object, str]] = []
    function_types = {"function_definition", "function_declaration", "generator_function_declaration", "method_definition"}

    def visit(node, parent_class: str | None = None) -> None:
        kind = node.type
        current_class = parent_class
        if kind in {"class_definition", "class_declaration"}:
            name = _node_name(node, source) or "<anonymous>"
            start_line = node.start_point.row + 1
            end_line = node.end_point.row + 1
            symbols.append(Symbol(name=name, kind="class", file_path=relative, start_line=start_line, end_line=end_line, snippet=_snippet(source, start_line, end_line)))
            class_nodes.append((node, name))
            current_class = name
        elif kind in function_types:
            name = _node_name(node, source)
            if name:
                start_line = node.start_point.row + 1
                end_line = node.end_point.row + 1
                symbols.append(Symbol(name=name, kind="method" if parent_class else "function", file_path=relative, start_line=start_line, end_line=end_line, parent=parent_class, snippet=_snippet(source, start_line, end_line)))
        elif kind == "variable_declarator" and node.child_by_field_name("value") and node.child_by_field_name("value").type == "arrow_function":
            name = _node_name(node, source)
            if name:
                value = node.child_by_field_name("value")
                start_line = value.start_point.row + 1
                end_line = value.end_point.row + 1
                symbols.append(Symbol(name=name, kind="method" if parent_class else "function", file_path=relative, start_line=start_line, end_line=end_line, parent=parent_class, snippet=_snippet(source, start_line, end_line)))
        elif kind in {"import_statement", "import_declaration"} and language != "Python":
            text = node.text.decode("utf-8", errors="replace")
            match = re.search(r"(?:from\s+|require\s*\(\s*|import\s*)['\"]([^'\"]+)", text)
            if match:
                imports.append(ImportRecord(file_path=relative, imported_name=match.group(1), line=node.start_point.row + 1))
        elif kind in {"import_from_statement", "import_statement"} and language == "Python":
            text = node.text.decode("utf-8", errors="replace")
            if text.lstrip().startswith("from "):
                match = re.match(r"from\s+([.\w]+)", text.strip())
                if match:
                    imports.append(ImportRecord(file_path=relative, imported_name=match.group(1), line=node.start_point.row + 1))
            else:
                imported = text.strip()[len("import "):].split(",")
                for item in imported:
                    name = item.strip().split(" as ", 1)[0]
                    if name:
                        imports.append(ImportRecord(file_path=relative, imported_name=name, line=node.start_point.row + 1))
        for child in node.named_children:
            visit(child, current_class)

    visit(tree.root_node)
    # Tree-sitter represents Python `import x` and `from x import y` with
    # slightly different node names; retain the regex fallback if no import
    # was captured so malformed-but-readable files remain useful.
    if not imports:
        _, imports = _python_file(path, relative) if language == "Python" else _javascript_file(path, relative)
    return symbols, imports


def _tree_relationships(path: Path, relative: str, language: str) -> list[Relationship]:
    """Extract call and inheritance edges from Tree-sitter nodes."""
    source = path.read_bytes()
    tree = _tree_parser(language).parse(source)
    relationships: list[Relationship] = []
    function_types = {"function_definition", "function_declaration", "generator_function_declaration", "method_definition"}

    def visit(node, current_scope: str = relative, current_class: str | None = None) -> None:
        scope = current_scope
        class_name = current_class
        if node.type in {"class_definition", "class_declaration"}:
            found = _node_name(node, source)
            if found:
                class_name = found
                scope = f"{relative}::{found}"
            if language == "Python":
                bases = node.child_by_field_name("superclasses") or next((child for child in node.named_children if child.type == "argument_list"), None)
                if bases:
                    for base in bases.named_children:
                        relationships.append(Relationship(source=scope, target=base.text.decode("utf-8", errors="replace"), kind="INHERITS", line=base.start_point.row + 1))
            else:
                heritage = next((child for child in node.named_children if child.type == "class_heritage"), None)
                if heritage:
                    for base in heritage.named_children:
                        if base.type in {"extends_clause", "implements_clause"}:
                            bases = base.named_children
                        else:
                            bases = [base] if base.type not in {"extends", "implements"} else []
                        for parent in bases:
                            relationships.append(Relationship(source=scope, target=parent.text.decode("utf-8", errors="replace"), kind="INHERITS", line=parent.start_point.row + 1))
        elif node.type in function_types:
            name = _node_name(node, source)
            if name:
                scope = f"{relative}::{class_name}.{name}" if class_name else f"{relative}::{name}"
        elif node.type == "variable_declarator" and node.child_by_field_name("value") and node.child_by_field_name("value").type == "arrow_function":
            name = _node_name(node, source)
            if name:
                scope = f"{relative}::{class_name}.{name}" if class_name else f"{relative}::{name}"
        elif node.type in {"call", "call_expression"}:
            callee = node.child_by_field_name("function")
            if callee is None and node.named_children:
                callee = node.named_children[0]
            if callee:
                relationships.append(Relationship(source=scope, target=callee.text.decode("utf-8", errors="replace"), kind="CALLS", line=node.start_point.row + 1))
        for child in node.named_children:
            visit(child, scope, class_name)

    visit(tree.root_node)
    return relationships


def _line_end(node: ast.AST, lines: list[str]) -> int:
    return int(getattr(node, "end_lineno", getattr(node, "lineno", 1)))


def _python_file(path: Path, relative: str) -> tuple[list[Symbol], list[ImportRecord]]:
    symbols: list[Symbol] = []
    imports: list[ImportRecord] = []
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return symbols, imports
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(Symbol(name=node.name, kind="class", file_path=relative, start_line=node.lineno, end_line=_line_end(node, source.splitlines())))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = next((item.name for item in ast.walk(tree) if isinstance(item, ast.ClassDef) and any(child is node for child in item.body)), None)
            symbols.append(Symbol(name=node.name, kind="method" if parent else "function", file_path=relative, start_line=node.lineno, end_line=_line_end(node, source.splitlines()), parent=parent))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(ImportRecord(file_path=relative, imported_name=alias.name, line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(ImportRecord(file_path=relative, imported_name=module, line=node.lineno))
    return symbols, imports


JS_CLASS = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")
JS_FUNCTION = re.compile(r"(?:function\s+|(?:async\s+)?(?:const|let|var)\s+)([A-Za-z_$][\w$]*)\s*(?:=\s*)?(?:async\s*)?\([^)]*\)\s*(?:=>|\{)")
JS_IMPORT = re.compile(r"(?:import\s+(?:.+?\s+from\s+)?|require\s*\(\s*)['\"]([^'\"]+)['\"]")


def _javascript_file(path: Path, relative: str) -> tuple[list[Symbol], list[ImportRecord]]:
    symbols: list[Symbol] = []
    imports: list[ImportRecord] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for number, line in enumerate(lines, 1):
        for match in JS_CLASS.finditer(line):
            symbols.append(Symbol(name=match.group(1), kind="class", file_path=relative, start_line=number, end_line=number))
        for match in JS_FUNCTION.finditer(line):
            symbols.append(Symbol(name=match.group(1), kind="function", file_path=relative, start_line=number, end_line=number))
        for match in JS_IMPORT.finditer(line):
            imports.append(ImportRecord(file_path=relative, imported_name=match.group(1), line=number))
    return symbols, imports


def is_evidence_document(relative: str) -> bool:
    name = Path(relative).name.lower()
    if name in DOCUMENT_NAMES or name.startswith("readme"):
        return True
    return name.startswith("requirements") and name.endswith(".txt")


def read_excerpt(path: Path, limit: int = EXCERPT_LINES) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:limit])


def is_test_path(path: str) -> bool:
    name = Path(path).name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name or "/tests/" in f"/{path.lower()}/"


def _resolve_import(import_record: ImportRecord, known_files: set[str]) -> str | None:
    imported = import_record.imported_name
    if not imported.startswith("."):
        candidate_names = {imported.replace(".", "/")}
    else:
        base = Path(import_record.file_path).parent / imported
        candidate_names = {str(base).replace("\\", "/")}
    for candidate in candidate_names:
        for suffix in ("", ".py", ".js", ".jsx", ".ts", ".tsx"):
            normalized = candidate.lstrip("./") + suffix
            if normalized in known_files:
                return normalized
    return None


def analyze_source(root: Path, repository: str, default_branch: str) -> AnalysisResult:
    files: list[str] = []
    folders: set[str] = set()
    symbols: list[Symbol] = []
    imports: list[ImportRecord] = []
    documents: list[DocumentExcerpt] = []
    file_relationships: list[Relationship] = []
    languages: Counter[str] = Counter()
    scanned_source_files = 0
    warnings: list[str] = []
    max_source_files = int(os.getenv("MAX_SOURCE_FILES", "1500"))
    max_source_file_bytes = int(os.getenv("MAX_SOURCE_FILE_BYTES", "1000000"))
    candidate_files: list[Path] = []
    for directory, subdirectories, filenames in os.walk(root):
        # Prune ignored directories before descending into them. Filtering only
        # after `rglob` still walks every file under node_modules/.git/etc.
        subdirectories[:] = [name for name in subdirectories if name not in IGNORED_DIRS]
        candidate_files.extend(Path(directory) / name for name in filenames)
    for path in sorted(candidate_files):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        folders.update(part_path.as_posix() for part_path in path.relative_to(root).parents if part_path.as_posix() != ".")
        if is_evidence_document(relative) and len(documents) < 40:
            try:
                if path.stat().st_size <= max_source_file_bytes:
                    documents.append(DocumentExcerpt(path=relative, text=read_excerpt(path)))
            except OSError:
                warnings.append(f"Could not read {relative}")
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            if any(item.path == relative for item in documents):
                files.append(relative)
            continue
        files.append(relative)
        language = LANGUAGES.get(path.suffix.lower())
        if not language:
            continue
        if scanned_source_files >= max_source_files:
            warnings.append(f"Stopped after {max_source_files} source files to keep the interactive demo responsive")
            break
        if path.stat().st_size > max_source_file_bytes:
            warnings.append(f"Skipped oversized source file: {relative}")
            continue
        scanned_source_files += 1
        languages[language] += 1
        try:
            found_symbols, found_imports = _tree_sitter_file(path, relative, language)
            symbols.extend(found_symbols)
            imports.extend(found_imports)
            relationships = _tree_relationships(path, relative, language)
            # Keep the per-file parser edges until the import resolution pass.
            # They are appended below after all files have been scanned.
            file_relationships.extend(relationships)
        except OSError:
            warnings.append(f"Could not read {relative}")
    known_files = set(files)
    relationships: list[Relationship] = list(file_relationships)
    for symbol in symbols:
        relationships.append(Relationship(source=symbol.file_path, target=symbol.name, kind="CONTAINS"))
    for item in imports:
        item.resolved_file = _resolve_import(item, known_files)
        relationships.append(Relationship(source=item.file_path, target=item.resolved_file or item.imported_name, kind="IMPORTS"))
    tests = sum(1 for file in files if is_test_path(file))
    source_files = scanned_source_files
    primary_language = languages.most_common(1)[0][0] if languages else None
    summary = AnalysisSummary(files=len(files), folders=len(folders), source_files=source_files, languages=dict(languages), primary_language=primary_language, classes=sum(1 for item in symbols if item.kind == "class"), functions=sum(1 for item in symbols if item.kind in {"function", "method"}), imports=len(imports), tests=tests)
    assign_symbol_ids(symbols)
    return AnalysisResult(repository=repository, default_branch=default_branch, summary=summary, files=files, symbols=symbols, imports=imports, relationships=relationships, documents=documents, warnings=warnings)
