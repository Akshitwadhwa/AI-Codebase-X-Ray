from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

from .analyzer import assign_symbol_ids, is_test_path
from .models import (
    AnalysisResult,
    EvidenceLink,
    FileView,
    GraphDocument,
    GraphEdge,
    GraphNode,
    GraphView,
    ImpactReport,
    ReviewFile,
    RiskFlag,
    Symbol,
    SymbolView,
    UnresolvedRef,
    ViewEdge,
    ViewNode,
)

FAN_IN_THRESHOLD = 5
ARCHITECTURE_NODE_CAP = 200
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_BARE_BUILTINS = {
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "range",
    "enumerate", "zip", "map", "filter", "sorted", "reversed", "sum", "min", "max", "abs",
    "any", "all", "isinstance", "issubclass", "type", "super", "open", "getattr", "setattr",
    "hasattr", "repr", "format", "id", "hex", "oct", "ord", "chr", "input", "vars", "dir",
    "callable", "iter", "next", "object", "Exception", "ValueError", "TypeError", "KeyError",
    "AttributeError", "RuntimeError", "NotImplementedError", "StopIteration",
}
_SELF = {"self", "cls", "this", "super"}


def to_networkx(document: GraphDocument) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for node in document.nodes:
        graph.add_node(node.id, **node.model_dump())
    for index, edge in enumerate(document.edges):
        graph.add_edge(edge.source, edge.target, key=str(index), **edge.model_dump())
    return graph


def _split_callee(raw: str) -> tuple[str | None, str]:
    text = raw.strip().split("(", 1)[0].strip()
    if not text:
        return None, ""
    parts = [part.strip() for part in text.split(".") if part.strip()]
    if not parts:
        return None, ""
    name = parts[-1]
    if not _IDENT.match(name):
        return None, ""
    qualifier = parts[-2] if len(parts) >= 2 else None
    return qualifier, name


def _scope_symbol(scope: str, symbols_by_file: dict[str, list[Symbol]]) -> Symbol | None:
    if "::" not in scope:
        return None
    file_path, qual = scope.split("::", 1)
    matches = []
    for symbol in symbols_by_file.get(file_path, []):
        qualified = f"{symbol.parent}.{symbol.name}" if symbol.kind == "method" and symbol.parent else symbol.name
        if qualified == qual:
            matches.append(symbol)
    if len(matches) == 1:
        return matches[0]
    return None


def _current_class(scope: str, caller: Symbol | None, symbols_by_file: dict[str, list[Symbol]]) -> str | None:
    if caller and caller.kind == "method":
        return caller.parent
    if caller and caller.kind == "class":
        return caller.name
    if "::" not in scope:
        return None
    file_path, qual = scope.split("::", 1)
    if "." in qual:
        return None
    for symbol in symbols_by_file.get(file_path, []):
        if symbol.kind == "class" and symbol.name == qual:
            return qual
    return None


def _resolve_name(
    *,
    file_path: str,
    raw: str,
    scope: str,
    caller: Symbol | None,
    symbols_by_file: dict[str, list[Symbol]],
    imported_files: dict[str, set[str]],
    kinds: set[str],
) -> tuple[str | None, str, list[str]]:
    qualifier, name = _split_callee(raw)
    if not name:
        return None, "unresolved", []
    current_class = _current_class(scope, caller, symbols_by_file)
    local = symbols_by_file.get(file_path, [])
    imported: list[Symbol] = []
    for imported_path in imported_files.get(file_path, ()):
        imported.extend(symbols_by_file.get(imported_path, []))

    def unique(found: list[Symbol], confidence: str) -> tuple[str | None, str, list[str]] | None:
        ids = list(dict.fromkeys(item.symbol_id for item in found if item.symbol_id))
        if len(ids) == 1:
            return ids[0], confidence, []
        if len(ids) > 1:
            return None, "unresolved", ids
        return None

    if qualifier in _SELF and current_class:
        found = [item for item in local if item.kind == "method" and item.parent == current_class and item.name == name]
        decided = unique(found, "exact")
        if decided:
            return decided

    if qualifier and qualifier not in _SELF:
        class_names = {item.name for item in [*local, *imported] if item.kind == "class" and item.name == qualifier}
        if class_names:
            found = [item for item in [*local, *imported] if item.kind == "method" and item.parent == qualifier and item.name == name]
            local_found = [item for item in found if item.file_path == file_path]
            decided = unique(local_found or found, "exact" if local_found else "import")
            if decided:
                return decided

    if qualifier is None:
        found = [item for item in local if item.kind in kinds and item.name == name]
        decided = unique(found, "file")
        if decided:
            return decided

    found = [item for item in imported if item.kind in kinds and item.name == name]
    decided = unique(found, "import")
    if decided:
        return decided
    return None, "unresolved", []


def _keep_unresolved(raw: str, candidates: list[str], known_names: set[str]) -> bool:
    if candidates:
        return True
    qualifier, name = _split_callee(raw)
    if not name or name in _BARE_BUILTINS:
        return False
    if qualifier in {None, *_SELF}:
        return True
    return name in known_names


def _conventional_test(test_path: str, source_path: str) -> bool:
    stem = Path(source_path).stem.lower()
    test_name = Path(test_path).name.lower()
    return test_name in {
        f"test_{stem}.py",
        f"{stem}_test.py",
        f"{stem}.test.js",
        f"{stem}.test.jsx",
        f"{stem}.test.ts",
        f"{stem}.test.tsx",
        f"{stem}.spec.js",
        f"{stem}.spec.jsx",
        f"{stem}.spec.ts",
        f"{stem}.spec.tsx",
    }


def build_graph(result: AnalysisResult) -> GraphDocument:
    assign_symbol_ids(result.symbols)
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    unresolved: list[UnresolvedRef] = []
    repo_id = f"repo:{result.repository}"
    nodes.append(GraphNode(id=repo_id, kind="repository", name=result.repository))

    folder_paths: set[str] = set()
    for file_path in result.files:
        parts = Path(file_path).parts[:-1]
        for index in range(1, len(parts) + 1):
            folder_paths.add("/".join(parts[:index]))
    for folder in sorted(folder_paths):
        nodes.append(GraphNode(id=f"folder:{folder}", kind="folder", name=Path(folder).name, file_path=folder))
    for file_path in result.files:
        nodes.append(GraphNode(
            id=f"file:{file_path}",
            kind="file",
            name=Path(file_path).name,
            file_path=file_path,
            is_test=is_test_path(file_path),
            symbol_count=sum(1 for item in result.symbols if item.file_path == file_path),
        ))

    for folder in folder_paths:
        parent = Path(folder).parent.as_posix()
        if parent == ".":
            edges.append(GraphEdge(source=repo_id, target=f"folder:{folder}", kind="CONTAINS"))
        else:
            edges.append(GraphEdge(source=f"folder:{parent}", target=f"folder:{folder}", kind="CONTAINS"))
    for file_path in result.files:
        parent = Path(file_path).parent.as_posix()
        if parent == ".":
            edges.append(GraphEdge(source=repo_id, target=f"file:{file_path}", kind="CONTAINS"))
        else:
            edges.append(GraphEdge(source=f"folder:{parent}", target=f"file:{file_path}", kind="CONTAINS"))

    symbols_by_file: dict[str, list[Symbol]] = defaultdict(list)
    classes: dict[tuple[str, str], list[Symbol]] = defaultdict(list)
    for symbol in result.symbols:
        symbols_by_file[symbol.file_path].append(symbol)
        nodes.append(GraphNode(
            id=symbol.symbol_id or "",
            kind=symbol.kind,
            name=symbol.name,
            file_path=symbol.file_path,
            parent=symbol.parent,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            snippet=symbol.snippet,
            is_test=is_test_path(symbol.file_path),
        ))
        edges.append(GraphEdge(source=f"file:{symbol.file_path}", target=symbol.symbol_id or "", kind="CONTAINS"))
        if symbol.kind == "class":
            classes[(symbol.file_path, symbol.name)].append(symbol)
    for symbol in result.symbols:
        if symbol.kind != "method" or not symbol.parent or not symbol.symbol_id:
            continue
        owners = classes.get((symbol.file_path, symbol.parent), [])
        chosen = next((item for item in owners if item.start_line <= symbol.start_line <= item.end_line), None)
        if chosen is None and len(owners) == 1:
            chosen = owners[0]
        if chosen and chosen.symbol_id:
            edges.append(GraphEdge(source=chosen.symbol_id, target=symbol.symbol_id, kind="CONTAINS"))

    imported_files: dict[str, set[str]] = defaultdict(set)
    for item in result.imports:
        if not item.resolved_file:
            continue
        imported_files[item.file_path].add(item.resolved_file)
        edges.append(GraphEdge(
            source=f"file:{item.file_path}",
            target=f"file:{item.resolved_file}",
            kind="IMPORTS",
            confidence="file",
            line=item.line,
            raw=item.imported_name,
        ))

    known_names = {symbol.name for symbol in result.symbols}
    for relationship in result.relationships:
        if relationship.kind not in {"CALLS", "INHERITS"}:
            continue
        file_path = relationship.source.split("::", 1)[0]
        caller = _scope_symbol(relationship.source, symbols_by_file)
        kinds = {"class"} if relationship.kind == "INHERITS" else {"function", "method"}
        symbol_id, confidence, candidates = _resolve_name(
            file_path=file_path,
            raw=relationship.target,
            scope=relationship.source,
            caller=caller,
            symbols_by_file=symbols_by_file,
            imported_files=imported_files,
            kinds=kinds,
        )
        source_id = caller.symbol_id if caller and caller.symbol_id else f"file:{file_path}"
        if symbol_id and confidence != "unresolved":
            if symbol_id == source_id and relationship.kind == "INHERITS":
                continue
            edges.append(GraphEdge(
                source=source_id,
                target=symbol_id,
                kind=relationship.kind,
                confidence=confidence,
                line=relationship.line,
                raw=relationship.target,
            ))
            continue
        if _keep_unresolved(relationship.target, candidates, known_names):
            unresolved.append(UnresolvedRef(
                source=source_id,
                raw=relationship.target,
                kind=relationship.kind,
                line=relationship.line,
                candidates=candidates,
            ))

    return GraphDocument(
        analysis_id=result.analysis_id or "",
        repository=result.repository,
        default_branch=result.default_branch,
        warnings=list(result.warnings),
        nodes=nodes,
        edges=edges,
        unresolved=unresolved,
    )


def _node_map(document: GraphDocument) -> dict[str, GraphNode]:
    return {node.id: node for node in document.nodes}


def _view_from_node(node: GraphNode, role: str | None = None) -> SymbolView:
    return SymbolView(
        symbol_id=node.id,
        name=node.name,
        kind=node.kind,
        file_path=node.file_path,
        parent=node.parent,
        start_line=node.start_line,
        end_line=node.end_line,
        snippet=node.snippet,
    )


def _link_from_edge(node: GraphNode, edge: GraphEdge, reason: str, depth: int = 1) -> EvidenceLink:
    return EvidenceLink(
        symbol_id=node.id if node.kind in {"class", "function", "method"} else None,
        name=node.name,
        kind=node.kind,
        file_path=node.file_path,
        raw=edge.raw,
        confidence=edge.confidence,
        line=edge.line,
        depth=depth,
        reason=reason,
        snippet=node.snippet,
    )


def _edge_data(graph: nx.MultiDiGraph, source: str, target: str) -> list[dict]:
    if not graph.has_edge(source, target):
        return []
    payload = graph.get_edge_data(source, target) or {}
    return [data for data in payload.values() if isinstance(data, dict)]


def _top_folder(path: str | None) -> str:
    if not path or "/" not in path:
        return ""
    return path.split("/", 1)[0]


def _add_reason(reasons: dict[str, list[str]], path: str | None, reason: str) -> None:
    if not path:
        return
    bucket = reasons.setdefault(path, [])
    if reason not in bucket:
        bucket.append(reason)


def impact_report(document: GraphDocument, symbol_id: str, depth: int = 1) -> ImpactReport | None:
    depth = 1 if depth < 1 else 2 if depth > 2 else depth
    nodes = _node_map(document)
    focus = nodes.get(symbol_id)
    if focus is None or focus.kind not in {"class", "function", "method"}:
        return None
    graph = to_networkx(document)
    file_id = f"file:{focus.file_path}" if focus.file_path else ""

    def callers_of(target: str, hop: int) -> list[EvidenceLink]:
        found: list[EvidenceLink] = []
        if target not in graph:
            return found
        for source in graph.predecessors(target):
            for data in _edge_data(graph, source, target):
                if data.get("kind") != "CALLS":
                    continue
                node = nodes.get(source)
                if node is None:
                    continue
                edge = GraphEdge.model_validate(data)
                found.append(_link_from_edge(node, edge, "direct caller" if hop == 1 else "indirect caller", hop))
        return found

    direct = callers_of(symbol_id, 1)
    callers = list(direct)
    if depth >= 2:
        seen = {item.symbol_id for item in direct if item.symbol_id}
        for item in direct:
            if not item.symbol_id:
                continue
            for nested in callers_of(item.symbol_id, 2):
                if nested.symbol_id == symbol_id or nested.symbol_id in seen:
                    continue
                seen.add(nested.symbol_id)
                callers.append(nested)

    callees: list[EvidenceLink] = []
    bases: list[EvidenceLink] = []
    if symbol_id in graph:
        for target in graph.successors(symbol_id):
            for data in _edge_data(graph, symbol_id, target):
                node = nodes.get(target)
                if node is None:
                    continue
                edge = GraphEdge.model_validate(data)
                if data.get("kind") == "CALLS":
                    callees.append(_link_from_edge(node, edge, "callee"))
                elif data.get("kind") == "INHERITS":
                    bases.append(_link_from_edge(node, edge, "base class"))

    imports: list[EvidenceLink] = []
    imported_by: list[tuple[str, GraphEdge]] = []
    for edge in document.edges:
        if edge.kind != "IMPORTS":
            continue
        if edge.source == file_id:
            target = nodes.get(edge.target)
            if target:
                imports.append(_link_from_edge(target, edge, "imports"))
        elif edge.target == file_id:
            source = nodes.get(edge.source)
            if source:
                imports.append(_link_from_edge(source, edge, "imported by"))
                imported_by.append((source.file_path or "", edge))

    tests: list[EvidenceLink] = []
    seen_tests: set[str] = set()
    for path, edge in imported_by:
        source = nodes.get(edge.source)
        if source and source.is_test and path not in seen_tests:
            seen_tests.add(path)
            tests.append(_link_from_edge(source, edge, "imports module"))
    if focus.file_path:
        for node in document.nodes:
            if node.kind != "file" or not node.is_test or not node.file_path or node.file_path in seen_tests:
                continue
            if _conventional_test(node.file_path, focus.file_path):
                seen_tests.add(node.file_path)
                tests.append(EvidenceLink(name=node.name, kind="file", file_path=node.file_path, reason="name match"))

    reasons: dict[str, list[str]] = {}
    _add_reason(reasons, focus.file_path, "defines symbol")
    for item in callers:
        if item.depth == 1:
            _add_reason(reasons, item.file_path, "direct caller")
    for path, _edge in imported_by:
        _add_reason(reasons, path, "imports module")
    for item in tests:
        _add_reason(reasons, item.file_path, "test evidence")
    if depth >= 2:
        for item in callers:
            if item.depth == 2:
                _add_reason(reasons, item.file_path, "indirect caller")
    review = [ReviewFile(path=path, reasons=items) for path, items in reasons.items()]

    unresolved = [
        EvidenceLink(name=item.raw, raw=item.raw, kind=item.kind, line=item.line, reason="unresolved", file_path=focus.file_path)
        for item in document.unresolved
        if item.source == symbol_id
    ]
    same_name = [
        node for node in document.nodes
        if node.name == focus.name and node.id != focus.id and node.kind in {"class", "function", "method"}
    ]
    direct_ids = {item.symbol_id or item.file_path for item in direct}
    outside = [item for item in direct if _top_folder(item.file_path) != _top_folder(focus.file_path)]
    risk: list[RiskFlag] = []
    if not tests:
        risk.append(RiskFlag(code="no_test_evidence", message="No test file imports this module or matches its name."))
    if len(direct_ids) >= FAN_IN_THRESHOLD:
        risk.append(RiskFlag(code="high_fan_in", message=f"{len(direct_ids)} direct callers. Review each callsite before changing the signature."))
    if outside:
        risk.append(RiskFlag(code="cross_folder", message=f"{len(outside)} direct caller(s) live outside {focus.file_path}'s top-level folder."))
    if unresolved:
        risk.append(RiskFlag(code="unresolved", message=f"{len(unresolved)} call or base could not be bound to a symbol."))
    if same_name:
        risk.append(RiskFlag(code="duplicate_name", message=f"{len(same_name)} other symbol(s) share the name {focus.name}."))
    if document.warnings:
        risk.append(RiskFlag(code="truncated_scan", message="The analysis warnings say this scan is incomplete."))

    return ImpactReport(
        symbol_id=symbol_id,
        symbol=_view_from_node(focus),
        callers=callers,
        callees=callees,
        bases=bases,
        imports=imports,
        tests=tests,
        review_files=review,
        risk=risk,
        unresolved=unresolved,
        warnings=list(document.warnings),
        depth=depth,
    )


def search_symbols(document: GraphDocument, query: str, limit: int = 50) -> list[SymbolView]:
    needle = query.strip().lower()
    if not needle:
        return []
    found = [
        _view_from_node(node)
        for node in document.nodes
        if node.kind in {"class", "function", "method"} and needle in node.name.lower()
    ]
    found.sort(key=lambda item: (item.file_path or "", item.start_line or 0, item.name))
    return found[:limit]


def file_view(document: GraphDocument, path: str) -> FileView | None:
    nodes = _node_map(document)
    file_node = nodes.get(f"file:{path}")
    if file_node is None:
        return None
    symbols = [
        _view_from_node(node)
        for node in document.nodes
        if node.file_path == path and node.kind in {"class", "function", "method"}
    ]
    symbols.sort(key=lambda item: item.start_line or 0)
    imports: list[EvidenceLink] = []
    imported_by: list[EvidenceLink] = []
    tests: list[EvidenceLink] = []
    for edge in document.edges:
        if edge.kind != "IMPORTS":
            continue
        if edge.source == file_node.id:
            target = nodes.get(edge.target)
            if target:
                imports.append(_link_from_edge(target, edge, "imports"))
        elif edge.target == file_node.id:
            source = nodes.get(edge.source)
            if source:
                imported_by.append(_link_from_edge(source, edge, "imported by"))
                if source.is_test:
                    tests.append(_link_from_edge(source, edge, "imports module"))
    return FileView(path=path, is_test=file_node.is_test, symbols=symbols, imports=imports, imported_by=imported_by, tests=tests)


def _view_node(node: GraphNode, role: str | None = None, depth: int | None = None) -> ViewNode:
    label = node.name
    if node.file_path and node.kind in {"class", "function", "method"}:
        label = f"{node.name}"
    return ViewNode(
        id=node.id,
        label=label,
        kind=node.kind,
        file_path=node.file_path,
        role=role,
        is_test=node.is_test,
        symbol_count=node.symbol_count,
        depth=depth,
    )


def _view_edges(document: GraphDocument, ids: set[str]) -> list[ViewEdge]:
    edges: list[ViewEdge] = []
    for edge in document.edges:
        if edge.source not in ids or edge.target not in ids:
            continue
        if edge.kind not in {"CONTAINS", "IMPORTS", "CALLS", "INHERITS"}:
            continue
        label = edge.kind if not edge.confidence else f"{edge.kind} · {edge.confidence}"
        edges.append(ViewEdge(source=edge.source, target=edge.target, kind=edge.kind, confidence=edge.confidence, label=label))
    return edges


def neighborhood_view(document: GraphDocument, symbol_id: str, depth: int = 1) -> GraphView | None:
    depth = 1 if depth < 2 else 2
    report = impact_report(document, symbol_id, depth=depth)
    if report is None:
        return None
    nodes = _node_map(document)
    focus = nodes[symbol_id]
    selected: dict[str, str] = {symbol_id: "focus"}
    depths: dict[str, int] = {}
    if focus.file_path:
        selected[f"file:{focus.file_path}"] = "file"
    for node in document.nodes:
        if node.kind == "class" and node.file_path == focus.file_path and node.name == focus.parent:
            selected[node.id] = "class"
    for edge in document.edges:
        if edge.kind == "CONTAINS" and edge.target == symbol_id:
            owner = nodes.get(edge.source)
            if owner and owner.kind == "class":
                selected[owner.id] = "class"
    for item in report.callers:
        if item.symbol_id:
            selected.setdefault(item.symbol_id, "caller")
            depths[item.symbol_id] = item.depth
    for item in report.callees:
        if item.symbol_id:
            selected.setdefault(item.symbol_id, "callee")
    for item in report.bases:
        if item.symbol_id:
            selected.setdefault(item.symbol_id, "base")
    for item in report.tests:
        if item.file_path:
            selected.setdefault(f"file:{item.file_path}", "test")
    for item in report.imports:
        if item.file_path and item.reason == "imports":
            selected.setdefault(f"file:{item.file_path}", "import")
    view_nodes = [
        _view_node(nodes[node_id], role, depths.get(node_id))
        for node_id, role in selected.items()
        if node_id in nodes
    ]
    return GraphView(view="neighborhood", nodes=view_nodes, edges=_view_edges(document, set(selected)))


def architecture_view(document: GraphDocument) -> GraphView:
    structural = [node for node in document.nodes if node.kind in {"repository", "folder", "file"}]
    import_edges = [edge for edge in document.edges if edge.kind == "IMPORTS"]
    contain_edges = [edge for edge in document.edges if edge.kind == "CONTAINS" and edge.source.startswith(("repo:", "folder:")) and edge.target.startswith(("folder:", "file:"))]
    truncated = False
    keep = {node.id for node in structural}
    if len(structural) > ARCHITECTURE_NODE_CAP:
        truncated = True
        degree: Counter[str] = Counter()
        for edge in import_edges:
            degree[edge.source] += 1
            degree[edge.target] += 1
        files = [node for node in structural if node.kind == "file"]
        files.sort(key=lambda node: (-degree[node.id], node.file_path or ""))
        keep = {node.id for node in files[:150]}
        for node_id in list(keep):
            path = node_id.removeprefix("file:")
            parent = Path(path).parent.as_posix()
            while parent != ".":
                keep.add(f"folder:{parent}")
                parent = Path(parent).parent.as_posix()
        repo_ids = [node.id for node in structural if node.kind == "repository"]
        keep.update(repo_ids)
    nodes = [_view_node(node, node.kind) for node in structural if node.id in keep]
    edges = [
        ViewEdge(source=edge.source, target=edge.target, kind=edge.kind, confidence=edge.confidence, label=edge.kind if not edge.confidence else f"{edge.kind} · {edge.confidence}")
        for edge in [*contain_edges, *import_edges]
        if edge.source in keep and edge.target in keep
    ]
    return GraphView(view="architecture", truncated=truncated, nodes=nodes, edges=edges)
