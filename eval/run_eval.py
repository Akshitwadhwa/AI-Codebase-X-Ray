from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from backend.analyzer import analyze_source
from backend.ask import answer_baseline, answer_question
from eval.report import render
from eval.score import score_answer, summarize

ROOT = Path(__file__).resolve().parent
REPOS = ROOT / "repos"
QUESTIONS = ROOT / "questions.json"
PROVIDERS = (
    {"id": "offline", "label": "Offline excerpts", "env": None},
    {"id": "openai", "label": "GPT", "env": "OPENAI_API_KEY"},
    {"id": "anthropic", "label": "Claude", "env": "ANTHROPIC_API_KEY"},
    {"id": "gemini", "label": "Gemini", "env": "GEMINI_API_KEY"},
    {"id": "grok", "label": "Grok", "env": "XAI_API_KEY"},
)


def run(provider: str, questions: list[dict], analyses: dict) -> list[dict]:
    rows = []
    for item in questions:
        result = analyses[item["repo"]]
        grounded = answer_question(result, item["question"], provider).answers[0]
        baseline = answer_baseline(result, item["question"], provider)
        for mode, answer in (("grounded", grounded), ("baseline", baseline)):
            scored = score_answer(answer.model_dump(), item)
            rows.append({
                "id": item["id"],
                "question": item["question"],
                "repo": item["repo"],
                "mode": mode,
                "provider": provider,
                "answerable": item["answerable"],
                "refused": answer.refused,
                "citations": [citation.path for citation in answer.citations],
                **scored,
            })
    return rows


def comparison(selected: str = "all") -> dict:
    load_dotenv()
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    catalog = list(PROVIDERS) if selected == "all" else [item for item in PROVIDERS if item["id"] == selected]
    if not catalog:
        known = ", ".join(item["id"] for item in PROVIDERS)
        raise SystemExit(f"Unknown provider {selected}. Choose all, {known}.")
    analyses = None
    providers = []
    rows = []
    for item in catalog:
        if item["env"] and not os.getenv(item["env"], "").strip():
            providers.append({"id": item["id"], "label": item["label"], "measured": False, "reason": item["env"]})
            continue
        if analyses is None:
            analyses = {name: analyze_source(REPOS / name, f"eval/{name}", "main") for name in sorted({question["repo"] for question in questions})}
        provider_rows = run(item["id"], questions, analyses)
        rows.extend(provider_rows)
        providers.append({
            "id": item["id"],
            "label": item["label"],
            "measured": True,
            "summary": [summarize(provider_rows, "baseline"), summarize(provider_rows, "grounded")],
        })
        print(item["label"])
        for summary in providers[-1]["summary"]:
            print(f"  {summary['mode']}: supported {summary['supported_rate']:.3f}, citation {summary['citation_hit_rate']:.3f}, abstention {summary['abstention_accuracy']:.3f}")
    return {
        "research_question": "Does grounding a repository question in the saved scan produce more supported answers than asking the same model with only the repository name?",
        "prompts": len(questions),
        "questions": [{"id": item["id"], "repo": item["repo"], "question": item["question"], "answerable": item["answerable"]} for item in questions],
        "providers": providers,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="all", help="all, offline, openai, anthropic, gemini, or grok")
    args = parser.parse_args()
    report = comparison(args.provider)
    (ROOT / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (ROOT / "results.md").write_text(render(report), encoding="utf-8")


if __name__ == "__main__":
    main()
