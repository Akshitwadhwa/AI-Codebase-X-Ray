from eval.report import render


def test_comparison_table_lists_measured_and_skipped_models() -> None:
    report = {
        "research_question": "Does grounding help?",
        "prompts": 1,
        "questions": [{"id": "q1", "repo": "billing-api", "question": "What database does this repository use?", "answerable": True}],
        "providers": [
            {
                "id": "offline",
                "label": "Offline excerpts",
                "measured": True,
                "summary": [
                    {"mode": "baseline", "questions": 1, "citation_hit_rate": 0.0, "abstention_accuracy": 0.0, "supported_rate": 0.0},
                    {"mode": "grounded", "questions": 1, "citation_hit_rate": 1.0, "abstention_accuracy": 1.0, "supported_rate": 1.0},
                ],
            },
            {"id": "openai", "label": "GPT", "measured": False, "reason": "OPENAI_API_KEY"},
            {"id": "gemini", "label": "Gemini", "measured": False, "error": "HTTP 429, free-tier daily quota for gemini-3.8-flash"},
        ],
        "rows": [
            {"provider": "offline", "id": "q1", "mode": "baseline", "supported": False},
            {"provider": "offline", "id": "q1", "mode": "grounded", "supported": True},
        ],
    }
    text = render(report)
    assert "| Offline excerpts | Without RAG | 1 | 0.000 | 0.000 | 0.000 |" in text
    assert "| Offline excerpts | With RAG | 1 | 1.000 | 1.000 | 1.000 |" in text
    assert "| GPT | Not measured | — | — | — | — |" in text
    assert "GPT needs `OPENAI_API_KEY`" in text
    assert "| Gemini | Call failed | — | — | — | — |" in text
    assert "Gemini: HTTP 429, free-tier daily quota for gemini-3.8-flash" in text
    assert "| What database does this repository use? | billing-api | yes | no | yes |" in text
