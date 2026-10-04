from pathlib import Path

from backend.analyzer import analyze_source


def test_analyzer_extracts_python_symbols_and_relationships(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("import json\n\nclass Service(Base):\n    def run(self):\n        return helper(json.dumps({}))\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("from app import Service\n", encoding="utf-8")
    result = analyze_source(tmp_path, "demo/app", "main")
    assert result.summary.files == 2
    assert result.summary.folders == 0
    assert result.summary.primary_language == "Python"
    assert result.summary.classes == 1
    assert result.summary.functions == 1
    assert result.summary.tests == 1
    assert any(item.kind == "CONTAINS" and item.target == "Service" for item in result.relationships)
    assert any(item.kind == "INHERITS" and item.target == "Base" for item in result.relationships)
    assert any(item.kind == "CALLS" and item.target == "helper" for item in result.relationships)


def test_analyzer_uses_tree_sitter_for_typescript(tmp_path: Path) -> None:
    (tmp_path / "service.ts").write_text(
        "import { helper } from './helper';\n"
        "class Service extends Base {\n"
        "  run(value: string) { return helper(value); }\n"
        "}\n",
        encoding="utf-8",
    )
    result = analyze_source(tmp_path, "demo/app", "main")
    assert result.summary.languages == {"TypeScript": 1}
    assert {item.name for item in result.symbols} == {"Service", "run"}
    assert any(item.kind == "INHERITS" and item.target == "Base" for item in result.relationships)
    assert any(item.kind == "CALLS" and item.target == "helper" for item in result.relationships)
