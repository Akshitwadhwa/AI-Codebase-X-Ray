from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.analyzer import analyze_source
from backend.ask import answer_baseline, answer_question
from eval.score import score_answer, summarize

ROOT = Path(__file__).resolve().parent
REPOS = ROOT / "repos"
QUESTIONS = ROOT / "questions.json"


def run(provider: str) -> dict:
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    analyses = {name: analyze_source(REPOS / name, f"eval/{name}", "main") for name in sorted({item["repo"] for item in questions})}
    rows = []
    for item in questions:
        result = analyses[item["repo"]]
        grounded = answer_question(result, item["question"], provider).answers[0]
        baseline = answer_baseline(result, item["question"], provider)
        for mode, answer in (("grounded", grounded), ("baseline", baseline)):
            scored = score_answer(answer.model_dump(), item)
            rows.append({"id": item["id"], "repo": item["repo"], "mode": mode, "provider": provider, "answerable": item["answerable"], "refused": answer.refused, "citations": [citation.path for citation in answer.citations], **scored})
    summary = [summarize(rows, "baseline"), summarize(rows, "grounded")]
    return {
        "provider": provider,
        "research_question": "Does grounding a repository question in the saved scan produce more supported answers than asking the same model with only the repository name?",
        "summary": summary,
        "rows": rows,
    }


def render(report: dict) -> str:
    lines = [
        "# Grounded questions evaluation",
        "",
        report["research_question"],
        "",
        f"Provider: `{report['provider']}`. The offline provider quotes matching scan lines and refuses when none match. GPT, Claude, Gemini, and Grok use this same runner when their API key is set. Baseline receives only the repository name. Grounded receives the saved scan, document excerpts, matching imports, and matching symbols.",
        "",
        "Citation hit is the share of answerable questions whose citations include a gold file. Abstention accuracy is how often the answer refused exactly when the scan has no gold file. Supported is a citation hit without a refusal on answerable questions, and a refusal on unanswerable questions.",
        "",
        "| Mode | Questions | Citation hit | Abstention accuracy | Supported |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in report["summary"]:
        lines.append(f"| {item['mode']} | {item['questions']} | {item['citation_hit_rate']:.3f} | {item['abstention_accuracy']:.3f} | {item['supported_rate']:.3f} |")
    lines.extend(["", "Cloud providers use the same script: `python -m eval.run_eval --provider openai`.", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="offline")
    args = parser.parse_args()
    report = run(args.provider)
    (ROOT / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (ROOT / "results.md").write_text(render(report), encoding="utf-8")
    for item in report["summary"]:
        print(f"{item['mode']}: supported {item['supported_rate']:.3f}, citation {item['citation_hit_rate']:.3f}, abstention {item['abstention_accuracy']:.3f}")


if __name__ == "__main__":
    main()
