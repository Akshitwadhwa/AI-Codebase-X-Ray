from pathlib import Path

from fastapi.testclient import TestClient

from backend.analyzer import analyze_source
from backend.ask import answer_baseline, answer_question, pack_context
from backend.main import app
from backend.store import NetworkXGraphStore
from eval.score import score_answer


def test_scan_keeps_gitignore_and_requirements(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".env\n.venv\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\nsqlalchemy\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("FastAPI backend that stores payments in SQLite.\n", encoding="utf-8")
    result = analyze_source(tmp_path, "demo/app", "main")
    saved = {item.path: item.text for item in result.documents}
    assert ".env" in saved[".gitignore"]
    assert "sqlalchemy" in saved["requirements.txt"]
    assert "SQLite" in saved["README.md"]
    assert ".gitignore" in result.files


def test_offline_grounded_answer_cites_sqlite_and_baseline_refuses(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("sqlalchemy\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "db.py").write_text("import sqlite3\n", encoding="utf-8")
    result = analyze_source(tmp_path, "demo/billing", "main")
    question = "What database does this repository use?"
    assert "sqlite3" in pack_context(result, question)
    grounded = answer_question(result, question, "offline").answers[0]
    baseline = answer_baseline(result, question, "offline")
    assert grounded.refused is False
    assert any(item.path == "app/db.py" for item in grounded.citations)
    assert baseline.refused is True
    assert score_answer(grounded.model_dump(), {"answerable": True, "paths": ["app/db.py"]})["supported"] is True
    assert score_answer(baseline.model_dump(), {"answerable": True, "paths": ["app/db.py"]})["supported"] is False


def test_offline_refuses_when_the_scan_has_no_database(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Meeting notes from October. No application code is in this folder.\n", encoding="utf-8")
    result = analyze_source(tmp_path, "demo/notes", "main")
    answer = answer_question(result, "What database does this repository use?", "offline").answers[0]
    assert answer.refused is True
    assert score_answer(answer.model_dump(), {"answerable": False, "paths": []})["abstention_correct"] is True


def test_vector_retrieval_stores_embeddings_and_finds_sqlite(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("sqlalchemy\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "db.py").write_text("import sqlite3\n", encoding="utf-8")
    result = analyze_source(tmp_path, "demo/billing", "main")
    result.analysis_id = "abc123abc123"
    from backend.services.orchestrator import Orchestrator

    service = Orchestrator(tmp_path)
    view = service.index(result)
    assert view.embedding == "local-hash-v1"
    assert view.chunks > 0
    assert (tmp_path / "abc123abc123.knowledge.json").is_file()
    found = service.retrieve(result, "What database does this repository use?")
    assert any(chunk.path == "app/db.py" and chunk.score > 0 for chunk in found.chunks)


def test_ask_endpoint_compares_two_offline_answers(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / ".gitignore").write_text(".env\n", encoding="utf-8")
    result = analyze_source(tmp_path / "src", "demo/app", "main")
    result.analysis_id = "abc123abc123"
    saved = NetworkXGraphStore(tmp_path / "analyses")
    saved.save_analysis(result)
    monkeypatch.setattr("backend.main.store", saved)
    client = TestClient(app)
    missing = client.post("/api/analyses/abc123abc123/ask", json={"question": "What does gitignore hide?", "provider": "openai"})
    assert missing.status_code == 400
    response = client.post("/api/analyses/abc123abc123/ask", json={"question": "What does gitignore hide?", "provider": "offline", "compare_with": "offline"})
    assert response.status_code == 400
    response = client.post("/api/analyses/abc123abc123/ask", json={"question": "What does gitignore hide?", "provider": "offline"})
    assert response.status_code == 200
    body = response.json()
    assert body["answers"][0]["refused"] is False
    assert ".gitignore" in body["answers"][0]["citations"][0]["path"]
    compared = client.post("/api/analyses/abc123abc123/ask", json={"question": "What does gitignore hide?", "provider": "offline", "compare_with": "without-rag"})
    assert compared.status_code == 200
    pair = compared.json()["answers"]
    assert pair[0]["mode"] == "grounded" and pair[0]["refused"] is False
    assert pair[1]["mode"] == "baseline" and pair[1]["refused"] is True
