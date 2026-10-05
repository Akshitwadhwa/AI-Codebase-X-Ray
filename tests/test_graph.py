from pathlib import Path

from fastapi.testclient import TestClient

from backend.analyzer import analyze_source
from backend.graph import build_graph, impact_report, neighborhood_view, to_networkx
from backend.main import app
from backend.models import AnalysisResult, ImportRecord, Relationship, Symbol
from backend.store import InvalidFilePath, NetworkXGraphStore, safe_relative_path


def _payment_tree(root: Path) -> None:
    (root / "billing").mkdir()
    (root / "other").mkdir()
    (root / "tests").mkdir()
    (root / "billing" / "helper.py").write_text("def charge_card(amount):\n    return amount\n", encoding="utf-8")
    (root / "billing" / "service.py").write_text(
        "from billing.helper import charge_card\n\n"
        "class BillingService:\n"
        "    def process_payment(self, amount):\n"
        "        self._charge(amount)\n"
        "        return handler()\n\n"
        "    def _charge(self, amount):\n"
        "        return charge_card(amount)\n",
        encoding="utf-8",
    )
    (root / "billing" / "api.py").write_text(
        "from billing.service import BillingService\n\n"
        "def checkout(service):\n"
        "    return service.process_payment(10)\n\n"
        "def pay(service):\n"
        "    return checkout(service)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_billing.py").write_text(
        "from billing.service import BillingService\n\n"
        "def test_checkout():\n"
        "    return BillingService\n",
        encoding="utf-8",
    )
    (root / "other" / "payments.py").write_text(
        "def process_payment():\n"
        "    return handler()\n",
        encoding="utf-8",
    )


def _focus(result):
    matches = [item for item in result.symbols if item.name == "process_payment" and item.parent == "BillingService"]
    assert len(matches) == 1
    return matches[0]


def test_process_payment_impact_lists_review_evidence(tmp_path: Path) -> None:
    _payment_tree(tmp_path)
    result = analyze_source(tmp_path, "demo/billing", "main")
    result.analysis_id = "abc123abc123"
    focus = _focus(result)
    other = next(item for item in result.symbols if item.name == "process_payment" and item.file_path == "other/payments.py")
    assert focus.symbol_id != other.symbol_id
    assert focus.snippet and "process_payment" in focus.snippet

    document = build_graph(result)
    graph = to_networkx(document)
    checkout = next(item for item in result.symbols if item.name == "checkout")
    assert graph.has_edge(checkout.symbol_id, focus.symbol_id)

    report = impact_report(document, focus.symbol_id, depth=1)
    assert report is not None
    assert report.symbol.file_path == "billing/service.py"
    assert report.symbol.parent == "BillingService"
    assert any(item.name == "checkout" and item.confidence == "import" for item in report.callers)
    assert any(item.name == "_charge" and item.confidence == "exact" for item in report.callees)
    assert any(item.file_path == "billing/helper.py" and item.reason == "imports" for item in report.imports)
    assert any(item.file_path == "tests/test_billing.py" and item.reason == "imports module" for item in report.tests)
    assert all(item.file_path != "other/payments.py" for item in report.callers)
    review = {item.path: item.reasons for item in report.review_files}
    assert "defines symbol" in review["billing/service.py"]
    assert "direct caller" in review["billing/api.py"]
    assert "test evidence" in review["tests/test_billing.py"]
    assert any(item.raw == "handler" for item in report.unresolved)
    assert any(item.code == "unresolved" for item in report.risk)
    assert any(item.code == "duplicate_name" for item in report.risk)
    assert not any(item.code == "no_test_evidence" for item in report.risk)


def test_truncated_scan_is_a_risk_flag(tmp_path: Path) -> None:
    _payment_tree(tmp_path)
    result = analyze_source(tmp_path, "demo/billing", "main")
    result.warnings.append("Stopped after 1500 source files to keep the interactive demo responsive")
    report = impact_report(build_graph(result), _focus(result).symbol_id, 1)
    assert report is not None
    assert any(item.code == "truncated_scan" for item in report.risk)
    assert report.warnings


def test_high_fan_in_and_cross_folder_are_distinct_callers() -> None:
    symbols = [
        Symbol(name="process_payment", kind="function", file_path="billing/service.py", start_line=1, end_line=3, snippet="def process_payment():\n    pass"),
    ]
    relationships = []
    files = ["billing/service.py"]
    for index in range(5):
        folder = "billing" if index < 4 else "checkout"
        path = f"{folder}/caller_{index}.py"
        files.append(path)
        symbols.append(Symbol(name=f"caller_{index}", kind="function", file_path=path, start_line=1, end_line=2))
        relationships.append(Relationship(source=f"{path}::caller_{index}", target="process_payment", kind="CALLS", line=2))
    result = AnalysisResult(
        analysis_id="abc123abc123",
        repository="demo/app",
        default_branch="main",
        summary={"files": len(files), "folders": 2, "source_files": len(files), "languages": {"Python": len(files)}, "primary_language": "Python", "classes": 0, "functions": len(symbols), "imports": 0, "tests": 0},
        files=files,
        symbols=symbols,
        imports=[ImportRecord(file_path=path, imported_name="billing.service", line=1, resolved_file="billing/service.py") for path in files if path != "billing/service.py"],
        relationships=relationships,
    )
    report = impact_report(build_graph(result), "sym:billing/service.py::process_payment", 1)
    assert report is not None
    assert len({item.symbol_id for item in report.callers}) == 5
    assert any(item.code == "high_fan_in" for item in report.risk)
    assert any(item.code == "cross_folder" for item in report.risk)
    assert any(item.code == "no_test_evidence" for item in report.risk)


def test_neighborhood_depth_adds_indirect_callers(tmp_path: Path) -> None:
    _payment_tree(tmp_path)
    result = analyze_source(tmp_path, "demo/billing", "main")
    document = build_graph(result)
    focus = _focus(result).symbol_id
    pay = next(item for item in result.symbols if item.name == "pay")
    near = neighborhood_view(document, focus, depth=1)
    far = neighborhood_view(document, focus, depth=2)
    assert near is not None and far is not None
    assert pay.symbol_id not in {node.id for node in near.nodes}
    indirect = next(node for node in far.nodes if node.id == pay.symbol_id)
    assert indirect.role == "caller"
    assert indirect.depth == 2
    roles = {node.role for node in far.nodes}
    assert {"focus", "caller", "callee", "class", "file"} <= roles


def test_file_path_rejects_traversal() -> None:
    assert safe_relative_path("billing/service.py") == "billing/service.py"
    for raw in ("../secret", "/etc/passwd", "billing/../../secret", "~/.ssh/id", "a\\b"):
        try:
            safe_relative_path(raw)
        except InvalidFilePath:
            continue
        raise AssertionError(raw)


def test_impact_api_reloads_saved_analysis(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _payment_tree(source)
    result = analyze_source(source, "demo/billing", "main")
    result.analysis_id = "abc123abc123"
    saved = NetworkXGraphStore(tmp_path / "analyses")
    saved.save_analysis(result)
    monkeypatch.setattr("backend.main.store", saved)
    focus = _focus(result).symbol_id
    client = TestClient(app)
    listing = client.get("/api/analyses")
    assert listing.status_code == 200
    assert listing.json()[0]["analysis_id"] == "abc123abc123"
    missing = client.get("/api/analyses/not-an-id")
    assert missing.status_code == 400
    impact = client.get(f"/api/analyses/abc123abc123/impact/{focus}")
    assert impact.status_code == 200
    body = impact.json()
    assert body["symbol"]["parent"] == "BillingService"
    assert any(item["name"] == "checkout" for item in body["callers"])
    graph = client.get("/api/analyses/abc123abc123/graph", params={"view": "neighborhood", "symbol_id": focus})
    assert graph.status_code == 200
    assert any(node["role"] == "focus" for node in graph.json()["nodes"])
    escaped = client.get("/api/analyses/abc123abc123/file", params={"path": "../secret"})
    assert escaped.status_code == 400
    viewed = client.get("/api/analyses/abc123abc123/file", params={"path": "billing/service.py"})
    assert viewed.status_code == 200
    assert any(item["name"] == "process_payment" for item in viewed.json()["symbols"])
    found = client.get("/api/analyses/abc123abc123/symbols", params={"q": "process_payment"})
    assert found.status_code == 200
    assert len(found.json()) == 2
